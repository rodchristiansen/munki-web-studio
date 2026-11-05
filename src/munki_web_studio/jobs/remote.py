"""Operations a worker on another machine runs with the platform's own tools.

Each one names the capability it needs, the repository folders it sends the
worker, the one allowlisted tool it runs with a fixed argument list, and the
folder whose files come back. A worker never runs a shell, a script from the
repository, or a tool outside its allowlist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RemoteSpec:
    name: str
    title: str
    capability: str
    tool: str
    # Arguments after the tool; "{staging}" stands for the job's staging folder.
    args: tuple[str, ...]
    # Repository folders copied into staging before the tool runs.
    inputs: tuple[str, ...]
    # The staging folder whose files are returned as artifacts.
    outputs: str
    timeout_seconds: int = 600
    max_attempts: int = 2
    # When set, the outputs replace this repository folder once `check` accepts
    # them. `check` gets the output folder and the studio's own reading of every
    # readable pkginfo, and returns a summary or raises ValueError.
    applies_to: str | None = None
    check: Callable[[Path, list[dict[str, Any]]], dict[str, int]] | None = None
    # Arguments a person may pass when starting the job.
    arguments: frozenset[str] = frozenset()
    # The argument naming an upload the worker receives as "{upload}", if any.
    upload_argument: str | None = None
    # Where the tool's standard output is saved, relative to staging, if anywhere.
    stdout_file: str | None = None
    # The output is a pkginfo that becomes a draft for review, with the upload
    # as its installer, instead of replacing anything.
    creates_draft: bool = False
