"""Write tests/fixtures/tagged/vectors.json from the fixture repository.

The Python and TypeScript tests both read this file, so the two encoders are
held to one set of expected outputs. Rebuild after changing the fixtures:

    uv run python -I tests/fixtures/build_tagged_vectors.py
"""

import datetime as dt
import json
from pathlib import Path

from munki_web_studio import tagged
from munki_web_studio.codec import ParseError, load

FIXTURES = Path(__file__).parent
OUT = FIXTURES / "tagged" / "vectors.json"

EDGE_CASES = {
    "edge/large-integers": {"big": 2**63 - 1, "negative": -(2**60), "safe": 2**53 - 1},
    "edge/reals": {"whole": 2.0, "fraction": 2.5, "tiny": 1e-300, "negative": -0.0},
    "edge/dates": {"date": dt.datetime(2026, 10, 20, 9, 30), "day": dt.date(2026, 10, 20)},
    "edge/data": {"empty": b"", "bytes": bytes(range(8))},
    "edge/key-order": {"10": "ten", "a": "a", "2": "two", "$type": "not a tag"},
    "edge/nesting": {"list": [[1, [2, [3]]], {"k": None}], "empty": {}, "none": []},
}


def build() -> None:
    vectors: list[dict[str, object]] = []
    for path in sorted(p for p in FIXTURES.rglob("*") if p.is_file()):
        relative = path.relative_to(FIXTURES).as_posix()
        if not relative.startswith(("repo/", "codec/")) or path.suffix == ".py":
            continue
        if path.name == ".gitkeep":
            continue
        try:
            data = load(path.read_bytes()).data
        except ParseError:
            continue
        vectors.append({"name": relative, "tagged": tagged.encode(data)})
    for name, data in EDGE_CASES.items():
        vectors.append({"name": name, "tagged": tagged.encode(data)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
