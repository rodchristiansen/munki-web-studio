"""Replacing a generated folder, such as catalogs, in one step.

The new files are written next to the folder and then swapped in with two
renames under the repository's writer lock, so readers see either the old set
or the new one. If the studio stops between the renames, `recover_swaps` puts
the old folder back on the next start. Nothing is touched until the new set has
been fully written, so a failed generation leaves the old catalogs as they were.
"""

import contextlib
import os
import secrets
import shutil
from pathlib import Path

from munki_web_studio.changes.apply import writer_lock
from munki_web_studio.repository.config import Repository

TMP = ".studio-new-"
OLD = ".studio-old-"


def _fsync_tree(root: Path) -> None:
    for path in [*root.rglob("*"), root]:
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    with contextlib.suppress(OSError):
        fd = os.open(root.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def swap_folder(repo: Repository, folder: str, source: Path, journal_dir: Path) -> None:
    """Make `repo.root/folder` hold exactly the files in `source`, plus any hidden
    files already there (such as `.gitkeep`), which the tools leave alone too."""
    target = repo.root / folder
    if target.is_symlink():
        raise OSError(f"{folder} is a link; refusing to replace it")
    token = secrets.token_hex(4)
    staged = repo.root / f".{folder}{TMP}{token}"
    old = repo.root / f".{folder}{OLD}{token}"
    with writer_lock(repo, journal_dir):
        shutil.copytree(source, staged, symlinks=False)
        if target.is_dir():
            for hidden in target.glob(".*"):
                if hidden.is_file() and not hidden.is_symlink():
                    shutil.copy2(hidden, staged / hidden.name)
        try:
            _fsync_tree(staged)
            had_old = target.exists()
            if had_old:
                os.replace(target, old)
            try:
                os.replace(staged, target)
            except BaseException:
                if had_old:
                    os.replace(old, target)
                raise
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)


def recover_swaps(repo: Repository, folder: str) -> list[str]:
    """Undo a swap that stopped half way, and clear leftovers. Returns what it did."""
    actions: list[str] = []
    target = repo.root / folder
    for leftover in sorted(repo.root.glob(f".{folder}{OLD}*")):
        if not target.exists():
            os.replace(leftover, target)
            actions.append(f"restored {folder} from {leftover.name}")
        else:
            shutil.rmtree(leftover, ignore_errors=True)
            actions.append(f"removed {leftover.name}")
    for leftover in sorted(repo.root.glob(f".{folder}{TMP}*")):
        shutil.rmtree(leftover, ignore_errors=True)
        actions.append(f"removed {leftover.name}")
    return actions
