import datetime as dt
import json
from pathlib import Path

import pytest
from tests.fixtures.build_tagged_vectors import EDGE_CASES, FIXTURES, OUT

from munki_web_studio import tagged
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import first_difference

VECTORS = json.loads(OUT.read_text(encoding="utf-8"))


def fixture_files() -> list[Path]:
    files: list[Path] = []
    for folder in ("repo", "codec"):
        for path in sorted((FIXTURES / folder).rglob("*")):
            if path.is_file() and path.suffix != ".py" and path.name != ".gitkeep":
                try:
                    load(path.read_bytes())
                except ParseError:
                    continue
                files.append(path)
    return files


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_every_fixture_round_trips_exactly_through_json(path: Path) -> None:
    data = load(path.read_bytes()).data
    wire = json.dumps(tagged.encode(data))
    assert first_difference(data, tagged.decode(json.loads(wire))) is None


@pytest.mark.parametrize("name", list(EDGE_CASES))
def test_edge_cases_round_trip(name: str) -> None:
    data = EDGE_CASES[name]
    assert (
        first_difference(data, tagged.decode(json.loads(json.dumps(tagged.encode(data))))) is None
    )


def test_vector_file_is_current() -> None:
    by_name = {v["name"]: v["tagged"] for v in VECTORS}
    for path in fixture_files():
        name = path.relative_to(FIXTURES).as_posix()
        assert by_name[name] == tagged.encode(load(path.read_bytes()).data), name


def test_tags() -> None:
    assert tagged.encode(2**53) == {"$type": "int", "value": str(2**53)}
    assert tagged.encode(2**53 - 1) == 2**53 - 1
    assert tagged.encode(2.0) == {"$type": "real", "value": "2.0"}
    assert tagged.encode(b"\x00") == {"$type": "data", "value": "AA=="}
    assert tagged.encode(dt.date(2026, 1, 2)) == {"$type": "day", "value": "2026-01-02"}
    assert tagged.encode({"b": 1, "a": 2}) == {"$type": "dict", "value": [["b", 1], ["a", 2]]}


def test_rejects_malformed_input() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        tagged.decode({"$type": "nope", "value": 1})
    with pytest.raises(ValueError, match="tagged"):
        tagged.decode({"name": "untagged object"})
    with pytest.raises(ValueError, match="Infinite"):
        tagged.encode(float("inf"))
    with pytest.raises(TypeError):
        tagged.decode(1.5)
