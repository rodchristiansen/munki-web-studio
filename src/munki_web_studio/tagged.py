"""Lossless JSON for repository values.

Plain JSON cannot carry everything a pkginfo holds: dates, binary data, a real
that happens to be whole (2.0), integers past 2^53 that JavaScript rounds, and
key order that JavaScript objects do not keep for number-like keys. Each of
those is sent as a tagged object instead:

    {"$type": "int",  "value": "9007199254740993"}   integer beyond 2^53 - 1
    {"$type": "real", "value": "2.0"}                 every real
    {"$type": "date", "value": "2026-10-20T09:30:00"} date and time
    {"$type": "day",  "value": "2026-10-20"}          date only (YAML)
    {"$type": "data", "value": "<base64>"}            binary data
    {"$type": "dict", "value": [["key", value], ...]} mapping, in order

Strings, booleans, null, safe integers, and lists are sent as themselves. The
API, the command line, and the MCP server all use this one encoding.
"""

import base64
import datetime as dt
import math
from typing import Any, cast

MAX_SAFE_INTEGER = 2**53 - 1

type Tagged = bool | int | str | list[Tagged] | dict[str, Any] | None


def encode(value: Any) -> Tagged:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        if abs(value) <= MAX_SAFE_INTEGER:
            return value
        return {"$type": "int", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Infinite or NaN reals cannot be stored")
        return {"$type": "real", "value": repr(value)}
    if isinstance(value, dt.datetime):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"$type": "day", "value": value.isoformat()}
    if isinstance(value, bytes | bytearray):
        return {"$type": "data", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        items = cast(dict[Any, Any], value).items()
        return {"$type": "dict", "value": [[str(k), encode(v)] for k, v in items]}
    if isinstance(value, list | tuple):
        return [encode(v) for v in cast(list[Any], value)]
    raise TypeError(f"Cannot encode {type(value).__name__}")


def decode(value: Any) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [decode(v) for v in cast(list[Any], value)]
    if isinstance(value, dict):
        tagged = cast(dict[str, Any], value)
        kind, raw = tagged.get("$type"), tagged.get("value")
        if set(tagged) != {"$type", "value"}:
            raise ValueError("Objects must be tagged with $type and value")
        match kind:
            case "int":
                return int(cast(str, raw))
            case "real":
                return float(cast(str, raw))
            case "date":
                return dt.datetime.fromisoformat(cast(str, raw))
            case "day":
                return dt.date.fromisoformat(cast(str, raw))
            case "data":
                return base64.b64decode(cast(str, raw), validate=True)
            case "dict":
                pairs = cast(list[list[Any]], raw)
                return {cast(str, k): decode(v) for k, v in pairs}
            case _:
                raise ValueError(f"Unknown $type {kind!r}")
    raise TypeError(f"Cannot decode {type(value).__name__}")
