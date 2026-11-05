"""Optional Git: status, committing a saved draft, and pushing, for a repository
that is a Git working tree. A repository that is not one works exactly as before.

Git can run programs a repository names: hooks, clean and smudge filters, diff
drivers, an fsmonitor, a pager, credential helpers, an ssh command, a GPG
program, and included config files that set any of these. A web service must
not run them on someone else's behalf, so every Git command here runs with the
system and global config ignored, hooks pointed at an empty folder, those
settings overridden, and only https and ssh remotes allowed. A repository whose
own config still sets one of them is refused, with the setting named, rather
than run half-safely.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from munki_web_studio.repository.config import Repository

TIMEOUT_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 180

# Repository config keys that would make Git run something, or read config from
# somewhere else, and that the overrides below do not already cancel (hooksPath,
# fsmonitor, pager, editor and askPass are overridden on every command, so a
# repository that sets them, as Husky does, still works). Matched lowercase.
UNSAFE_CONFIG = re.compile(
    r"^(?:"
    r"filter\..+\.(?:clean|smudge|process)"
    r"|diff\..+\.(?:textconv|command)"
    r"|merge\..+\.driver"
    r"|core\.(?:sshcommand|gitproxy)"
    r"|credential\..*"
    r"|gpg\..*program"
    r"|include\.path|includeif\..+\.path"
    r"|sequence\.editor|uploadpack\..*|receive\..*|remote\..+\.(?:uploadpack|receivepack|proxy)"
    r"|url\..+\.(?:insteadof|pushinsteadof)"
    r"|http\..*proxy|http\.proxy"
    r")$"
)

OVERRIDES = (
    "core.fsmonitor=false",
    "core.pager=cat",
    "core.editor=false",
    "core.askPass=",
    "credential.helper=",
    "diff.external=",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "protocol.ssh.allow=always",
    "submodule.recurse=false",
    "status.showUntrackedFiles=all",
)


class GitError(Exception):
    pass


@dataclass
class GitStatus:
    enabled: bool
    reason: str | None = None
    branch: str | None = None
    head: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    changed: list[str] = field(default_factory=lambda: list[str]())


class Git:
    def __init__(self, repo: Repository, state_dir: Path) -> None:
        self.repo = repo
        self.hooks = state_dir / "git-no-hooks"
        self.home = state_dir / "git-home"

    def _env(self) -> dict[str, str]:
        self.home.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "LANG": "C",
        }
        # ssh needs the service account's agent to push over ssh.
        for name in ("SSH_AUTH_SOCK", "SYSTEMROOT", "TEMP", "TMP"):
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def run(self, *args: str, timeout: float = TIMEOUT_SECONDS, check: bool = True) -> str:
        self.hooks.mkdir(parents=True, exist_ok=True)
        overrides = [f"core.hooksPath={self.hooks}", *OVERRIDES]
        command = ["git", "-C", str(self.repo.root)]
        for item in overrides:
            command += ["-c", item]
        command += list(args)
        try:
            done = subprocess.run(  # noqa: S603 - fixed git binary, argument list, no shell
                command,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise GitError("Git is not installed on the studio's server") from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took longer than {int(timeout)} seconds") from None
        if check and done.returncode != 0:
            message = (done.stderr or done.stdout).strip().splitlines()
            raise GitError(message[-1] if message else f"git {args[0]} failed")
        return done.stdout

    def is_repository(self) -> bool:
        try:
            top = self.run("rev-parse", "--show-toplevel").strip()
        except GitError:
            return False
        return Path(top).resolve() == self.repo.root.resolve()

    def unsafe_settings(self) -> list[str]:
        """Repository config that would run a program or pull in other config."""
        listing = self.run("config", "--local", "--list", "--name-only", "--null", check=False)
        keys = {k.lower() for k in listing.split("\0") if k}
        return sorted(k for k in keys if UNSAFE_CONFIG.match(k))

    def check(self) -> str | None:
        """Why Git cannot be used here, or None when it can."""
        if not self.is_repository():
            return "not a Git repository"
        unsafe = self.unsafe_settings()
        if unsafe:
            return (
                "the repository's Git config sets "
                + ", ".join(unsafe)
                + ", which would run programs; the studio leaves Git alone here"
            )
        return None

    def status(self) -> GitStatus:
        try:
            reason = self.check()
        except GitError as error:
            return GitStatus(enabled=False, reason=str(error))
        if reason:
            return GitStatus(enabled=False, reason=reason)
        status = GitStatus(enabled=True)
        branch = self.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        status.branch = branch or None
        head = self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).strip()
        status.head = head or None
        upstream = self.run(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        ).strip()
        if upstream and status.head:
            status.upstream = upstream
            counts = self.run("rev-list", "--left-right", "--count", "@{upstream}...HEAD").split()
            status.behind, status.ahead = int(counts[0]), int(counts[1])
        porcelain = self.run("status", "--porcelain=v1", "-z", "--no-renames")
        status.changed = sorted({entry[3:] for entry in porcelain.split("\0") if len(entry) > 3})
        return status

    def commit(self, paths: list[str], message: str, author: str, email: str) -> str:
        """Commit exactly these paths, whatever else is staged, and return the commit."""
        if not paths:
            raise GitError("Nothing to commit")
        reason = self.check()
        if reason:
            raise GitError(f"Git is not available: {reason}")
        self.run("add", "--", *paths)
        self.run(
            "-c",
            f"user.name={author}",
            "-c",
            f"user.email={email}",
            "commit",
            "--no-verify",
            "--quiet",
            "--only",
            "-m",
            message,
            "--",
            *paths,
        )
        return self.run("rev-parse", "HEAD").strip()

    def committed_paths(self, commit: str) -> list[str]:
        listing = self.run(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", commit
        )
        return sorted(p for p in listing.split("\0") if p)

    def push(self) -> str:
        """Push the current branch to its upstream. Returns what Git reported."""
        status = self.status()
        if not status.enabled:
            raise GitError(f"Git is not available: {status.reason}")
        if not status.branch or not status.upstream:
            raise GitError("The current branch has no upstream to push to")
        remote, _, branch = status.upstream.partition("/")
        url = self.run("remote", "get-url", "--push", remote).strip()
        if not url.startswith(("https://", "ssh://", "git@")) and not re.match(
            r"^[\w.-]+@[\w.-]+:", url
        ):
            raise GitError("Only https and ssh remotes can be pushed to")
        self.run(
            "push",
            "--no-verify",
            "--porcelain",
            remote,
            f"HEAD:refs/heads/{branch}",
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return f"Pushed {status.branch} to {status.upstream}"
