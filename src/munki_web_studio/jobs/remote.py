"""Operations a worker on another machine runs with the platform's own tools.

Each one names the capability it needs, the repository folders it sends the
worker, the one allowlisted tool it runs with a fixed argument list, and the
folder whose files come back. A worker never runs a shell, a script from the
repository, or a tool outside its allowlist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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
    # them. `check` gets the output folder and the number of pkginfo files, and
    # returns a summary or raises ValueError.
    applies_to: str | None = None
    check: Callable[[Path, int], dict[str, int]] | None = None
    # Arguments a person may pass when starting the job.
    arguments: frozenset[str] = frozenset()
