import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio import tagged
from munki_web_studio.mcp import create_mcp
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.scanner import scan
from munki_web_studio.services import Services

READ_TOOLS = {
    "search_packages",
    "get_pkginfo",
    "get_manifest",
    "where_used",
    "explain_conditions",
    "validate_repository",
    "list_changes",
}
DRAFT_TOOLS = {"draft_pkginfo_edit", "draft_manifest_edit", "draft_catalog_move"}


@pytest.fixture
def services(tmp_path: Path) -> Services:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    repo = Repository(id="fixture", name="Fixture", root=root.resolve())
    index = Index(tmp_path / "index.sqlite")
    scan(repo, index)
    return Services(Settings(repositories=(repo,), state_dir=tmp_path), index)


def call(services: Services, tool: str, **arguments: Any) -> Any:
    async def run() -> Any:
        async with Client(create_mcp(services)) as client:
            result = await client.call_tool(tool, arguments)
            return result.structured_content

    return asyncio.run(run())


def test_lists_every_read_tool_and_marks_it_read_only(services: Services) -> None:
    async def run() -> list[Any]:
        async with Client(create_mcp(services)) as client:
            return await client.list_tools()

    tools = asyncio.run(run())
    assert {t.name for t in tools} == READ_TOOLS | DRAFT_TOOLS
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (tool.name in READ_TOOLS)
        assert not tool.annotations.destructive_hint


def test_search_and_get_pkginfo(services: Services) -> None:
    found = call(services, "search_packages", query="editor", limit=10)
    paths = [i["path"] for i in found["items"]]
    assert "pkgsinfo/apps/creative/ExampleEditor-2.10.plist" in paths
    assert call(services, "search_packages", catalog="production")["total"] > 0
    pkginfo = call(services, "get_pkginfo", path="pkgsinfo/apps/creative/ExampleEditor-2.10.plist")
    assert tagged.decode(pkginfo["data"])["installed_size"] == 9007199254740993


def test_results_are_bounded(services: Services) -> None:
    result = call(services, "search_packages", limit=2)
    assert len(result["items"]) == 2 and result["total"] > 2


def test_manifest_where_used_conditions_and_validation(services: Services) -> None:
    manifest = call(services, "get_manifest", path="manifests/groups/design-lab")
    assert manifest["conditional"][0]["condition"]["rule"].startswith('machine type is "laptop"')
    used = call(services, "where_used", name="ExampleEditor")
    assert any(r["source"] == "manifests/groups/design-lab" for r in used["items"])
    rules = call(services, "explain_conditions", path="manifests/groups/design-lab")
    assert rules["result"][0]["unknown_facts"] == []
    report = call(services, "validate_repository", severity="error")
    assert report["errors"] > 0 and all(i["severity"] == "error" for i in report["items"])


def test_paths_outside_the_repository_fail(services: Services) -> None:
    async def run() -> Any:
        async with Client(create_mcp(services)) as client:
            return await client.call_tool(
                "get_pkginfo", {"path": "../outside"}, raise_on_error=False
            )

    assert asyncio.run(run()).is_error


def test_layout_resource(services: Services) -> None:
    async def run() -> Any:
        async with Client(create_mcp(services)) as client:
            return await client.read_resource("studio://layout")

    contents = asyncio.run(run())
    layout = json.loads(contents[0].text)
    assert layout["folders"]["pkgsinfo"] == "pkginfo"
    assert "hostname" in layout["known_condition_facts"]
