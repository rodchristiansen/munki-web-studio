"""References between repository objects, where-used lookups, and validation.

Works from the index alone: each entry's summary lists what it refers to, so no
file is re-read. The platform module says which fields hold references.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from munki_web_studio import conditions, platform
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index, IndexedObject
from munki_web_studio.repository.paths import PathEscapeError, contained


@dataclass(frozen=True)
class Reference:
    source: str
    field: str
    target: str
    condition: str | None


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" or "warning"
    code: str
    path: str
    message: str
    field: str | None = None
    target: str | None = None


class Graph:
    def __init__(self, objects: list[IndexedObject]) -> None:
        self.objects = objects
        self.packages: dict[str, list[IndexedObject]] = {}
        self.manifests: dict[str, IndexedObject] = {}
        for obj in objects:
            if obj.status != "ok":
                continue
            if obj.kind == "pkginfo" and obj.name:
                self.packages.setdefault(obj.name, []).append(obj)
            elif obj.kind == "manifest":
                self.manifests[manifest_name(obj.path)] = obj

    def references(self, obj: IndexedObject) -> list[Reference]:
        refs: list[dict[str, Any]] = obj.summary.get("refs", [])
        return [Reference(obj.path, r["field"], r["target"], r.get("condition")) for r in refs]

    def resolve_package(self, item: str) -> str | None:
        if item in self.packages:
            return item
        name, version = platform.split_item(item)
        if name in self.packages and (
            version is None or any(o.version == version for o in self.packages[name])
        ):
            return name
        return None

    def where_used(self, name: str) -> list[Reference]:
        found: list[Reference] = []
        for obj in self.objects:
            if obj.status != "ok":
                continue
            for ref in self.references(obj):
                if ref.field in platform.INCLUDE_SECTIONS:
                    if ref.target == name and obj.kind == "manifest":
                        found.append(ref)
                elif self.resolve_package(ref.target) == name:
                    found.append(ref)
        return found

    def include_cycles(self) -> list[list[str]]:
        edges = {
            name: [
                r.target
                for r in self.references(obj)
                if r.field in platform.INCLUDE_SECTIONS and r.target in self.manifests
            ]
            for name, obj in self.manifests.items()
        }
        cycles: list[list[str]] = []
        seen_cycles: set[frozenset[str]] = set()
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for nxt in edges.get(node, []):
                if state.get(nxt) == 1:
                    cycle = stack[stack.index(nxt) :]
                    key = frozenset(cycle)
                    if key not in seen_cycles:
                        seen_cycles.add(key)
                        cycles.append([*cycle, nxt])
                elif nxt not in state:
                    visit(nxt)
            stack.pop()
            state[node] = 2

        for node in sorted(edges):
            if node not in state:
                visit(node)
        return cycles


def object_conditions(graph: Graph, obj: IndexedObject) -> list[str]:
    """Each distinct condition an object uses, in order of first appearance."""
    found = [r.condition for r in graph.references(obj) if r.condition]
    return list(dict.fromkeys(found))


def manifest_name(path: str) -> str:
    """Manifests refer to each other by path under manifests/, without extension
    for Munki and with it optional for Cimian."""
    relative = path.split("/", 1)[1] if "/" in path else path
    for suffix in platform.SUFFIXES or ():
        if relative.endswith(suffix):
            return relative[: -len(suffix)]
    return relative


def graph_for(index: Index, repo: Repository) -> Graph:
    return Graph(index.all(repo.id))


def validate(index: Index, repo: Repository) -> list[Issue]:
    graph = graph_for(index, repo)
    issues: list[Issue] = []
    for obj in graph.objects:
        if obj.status != "ok":
            issues.append(Issue("error", "parse-error", obj.path, obj.error or "Unreadable file"))
            continue
        for ref in graph.references(obj):
            if ref.field in platform.INCLUDE_SECTIONS:
                if ref.target not in graph.manifests:
                    issues.append(
                        Issue(
                            "error",
                            "missing-manifest",
                            obj.path,
                            f"Included manifest {ref.target!r} does not exist",
                            ref.field,
                            ref.target,
                        )
                    )
            elif graph.resolve_package(ref.target) is None:
                issues.append(
                    Issue(
                        "error" if obj.kind == "manifest" else "warning",
                        "missing-package",
                        obj.path,
                        f"No package named {ref.target!r}",
                        ref.field,
                        ref.target,
                    )
                )
        payloads = [obj.summary.get("payload"), *obj.summary.get("payloads", [])]
        for payload in payloads if obj.kind == "pkginfo" else []:
            if payload and not _exists(repo.root, payload):
                issues.append(
                    Issue(
                        "warning",
                        "missing-payload",
                        obj.path,
                        f"Payload {payload!r} is missing",
                        "payload",
                        payload,
                    )
                )
    for obj in graph.objects:
        if obj.status != "ok":
            continue
        for problem in obj.summary.get("problems", []):
            issues.append(Issue(problem["severity"], problem["code"], obj.path, problem["message"]))
        for text in object_conditions(graph, obj):
            report = conditions.analyze(text)
            if report.error:
                issues.append(
                    Issue(
                        "error",
                        "condition-syntax",
                        obj.path,
                        f"Condition {text!r}: {report.error} at column {report.column}",
                        "condition",
                        text,
                    )
                )
            for fact in report.unknown_facts:
                issues.append(
                    Issue(
                        "warning",
                        "unknown-fact",
                        obj.path,
                        f"Condition uses {fact!r}, which is not a built-in fact; "
                        "a condition script must provide it",
                        "condition",
                        fact,
                    )
                )
    for cycle in graph.include_cycles():
        first = graph.manifests[cycle[0]].path
        issues.append(
            Issue(
                "error",
                "include-cycle",
                first,
                "Included manifests form a loop: " + " → ".join(cycle),
            )
        )
    return sorted(issues, key=lambda i: (i.severity != "error", i.path, i.code, i.target or ""))


def _exists(root: Path, relative: str) -> bool:
    try:
        return contained(root, relative).is_file()
    except PathEscapeError:
        return False
