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
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from munki_web_studio.repository.config import Repository

TIMEOUT_SECONDS = 60
PUSH_TIMEOUT_SECONDS = 180

# The repository config keys the studio accepts. Git can be told to run a
# program, or to read more config, by many keys, and new ones appear in new Git
# versions, so anything not listed here turns Git off for the repository
# rather than being trusted. hooksPath, fsmonitor, pager and editor are also
# overridden on every command, so a repository that sets them, as Husky does,
# still works. Matched against lowercase keys.
ALLOWED_CONFIG = re.compile(
    r"^(?:"
    r"core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|ignorecase"
    r"|precomposeunicode|autocrlf|eol|safecrlf|symlinks|quotepath|untrackedcache"
    r"|longpaths|checkstat|trustctime|sharedrepository|hookspath|fsmonitor|pager|editor"
    r"|abbrev|whitespace|commentchar|preloadindex|fscache)"
    r"|user\.(?:name|email|signingkey)"
    r"|init\.defaultbranch"
    r"|extensions\.objectformat"
    r"|branch\.[^.]+(?:\.[^.]+)*\.(?:remote|merge|rebase|pushremote|description)"
    r"|remote\.[^.]+\.(?:url|pushurl|fetch|push|tagopt|prune|mirror|skipdefaultupdate)"
    r"|pull\.(?:rebase|ff)|push\.(?:default|autosetupremote|followtags)"
    r"|fetch\.(?:prune|prunetags)"
    r"|gc\.\w+|maintenance\.(?:auto|strategy)"
    r"|commit\.gpgsign|tag\.gpgsign"
    r"|diff\.(?:algorithm|renames|renamelimit|mnemonicprefix|colormoved|indentheuristic)"
    r"|merge\.(?:conflictstyle|ff|renames)"
    r"|color\.\w+(?:\.\w+)?|advice\.\w+|status\.\w+|log\.\w+|rerere\.enabled"
    r")$"
)
SCOPES = frozenset({"local", "worktree"})

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


# Only ordinary files are committed; a path that is a link or a submodule in
# the last commit is refused rather than given file contents.
REGULAR_MODES = frozenset({"100644", "100755"})


class GitError(Exception):
    pass


def _redact(text: str) -> str:
    """Drop any user name or token in a URL before Git's words reach a browser."""
    return re.sub(r"(\w+://)[^/@\s]+@", r"\1***@", text)[:500]


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
            # Paths are file names, never patterns or magic like ":(glob)".
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "LANG": "C",
        }
        # ssh needs the service account's agent to push over ssh.
        for name in ("SSH_AUTH_SOCK", "SYSTEMROOT", "TEMP", "TMP"):
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def _exec(
        self,
        args: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
        timeout: float = TIMEOUT_SECONDS,
        check: bool = True,
    ) -> bytes:
        self.hooks.mkdir(parents=True, exist_ok=True)
        overrides = [f"core.hooksPath={self.hooks}", *OVERRIDES]
        command = ["git", "-C", str(self.repo.root)]
        for item in overrides:
            command += ["-c", item]
        command += list(args)
        try:
            done = subprocess.run(  # noqa: S603 - fixed git binary, argument list, no shell
                command,
                env=self._env() | (env or {}),
                capture_output=True,
                input=stdin if stdin is not None else b"",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise GitError("Git is not installed on the studio's server") from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took longer than {int(timeout)} seconds") from None
        if check and done.returncode != 0:
            text = (done.stderr or done.stdout).decode("utf-8", "replace")
            message = text.strip().splitlines()
            raise GitError(_redact(message[-1]) if message else f"git {args[0]} failed")
        return done.stdout

    def run(self, *args: str, timeout: float = TIMEOUT_SECONDS, check: bool = True) -> str:
        return self._exec(args, timeout=timeout, check=check).decode("utf-8", "replace")

    def is_repository(self) -> bool:
        try:
            top = self.run("rev-parse", "--show-toplevel").strip()
        except GitError:
            return False
        return Path(top).resolve() == self.repo.root.resolve()

    def unsafe_settings(self) -> list[str]:
        """Repository config keys outside the allowlist, from every scope Git reads
        with system and global config ignored: local and per-worktree."""
        listing = self.run("config", "--list", "--show-scope", "--name-only", "--null")
        fields = listing.split("\0")
        found: set[str] = set()
        for scope, key in zip(fields[0::2], fields[1::2], strict=False):
            if scope in SCOPES and key and not ALLOWED_CONFIG.match(key.lower()):
                found.add(key.lower())
        return sorted(found)

    def check(self) -> str | None:
        """Why Git cannot be used here, or None when it can."""
        if not self.is_repository():
            return "not a Git repository"
        unsafe = self.unsafe_settings()
        if unsafe:
            return (
                "the repository's Git config sets "
                + ", ".join(unsafe)
                + ", which the studio does not allow because it could run programs; "
                "the studio leaves Git alone here"
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

    def commit(self, files: dict[str, bytes], message: str, author: str, email: str) -> str:
        """Commit exactly these files with exactly these contents, and return the
        commit. The commit is built with plumbing from the bytes given, not from
        the working tree, so nothing that changes on disk meanwhile can slip in;
        other staged or modified files are left as they were. No hooks run."""
        if not files:
            raise GitError("Nothing to commit")
        reason = self.check()
        if reason:
            raise GitError(f"Git is not available: {reason}")
        head = self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).strip()
        paths = sorted(files)
        old = self._entries(("ls-tree", "-z", head, "--", *paths)) if head else {}
        entries: list[str] = []
        for path in paths:
            blob = self._exec(("hash-object", "-w", "--stdin"), stdin=files[path]).decode().strip()
            mode = old[path][0] if path in old else "100644"
            if mode not in REGULAR_MODES:
                raise GitError(f"{path} is not a regular file in the last commit")
            entries.append(f"{mode} {blob}\t{path}")
        index = self.hooks.parent / f"git-index-{secrets.token_hex(6)}"
        scratch = {"GIT_INDEX_FILE": str(index)}
        try:
            if head:
                self._exec(("read-tree", head), env=scratch)
            else:
                self._exec(("read-tree", "--empty"), env=scratch)
            listing = "".join(f"{e}\0" for e in entries).encode()
            self._exec(("update-index", "-z", "--index-info"), stdin=listing, env=scratch)
            tree = self._exec(("write-tree",), env=scratch).decode().strip()
        finally:
            index.unlink(missing_ok=True)
        if head and tree == self.run("rev-parse", f"{head}^{{tree}}").strip():
            raise GitError("The saved files already match the last commit")
        people = {
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": email,
        }
        parents = ("-p", head) if head else ()
        commit = (
            self._exec(("commit-tree", tree, *parents), stdin=message.encode(), env=people)
            .decode()
            .strip()
        )
        if self.committed_paths(commit) != paths:
            raise GitError("The commit did not hold exactly the saved files; nothing was changed")
        # Moves the branch only if nobody else committed meanwhile.
        self._exec(("update-ref", "-m", "studio: commit saved files", "HEAD", commit, head))
        self._sync_index(paths, old, entries)
        return commit

    def _entries(self, args: tuple[str, ...]) -> dict[str, tuple[str, str]]:
        """Path to (mode, object) from `ls-tree -z` or `ls-files -s -z` output."""
        found: dict[str, tuple[str, str]] = {}
        for record in self.run(*args).split("\0"):
            if "\t" not in record:
                continue
            meta, path = record.split("\t", 1)
            fields = meta.split()
            obj = fields[2] if fields[1] == "blob" else fields[1]
            found[path] = (fields[0], obj)
        return found

    def _sync_index(
        self, paths: list[str], old: dict[str, tuple[str, str]], entries: list[str]
    ) -> None:
        """Point the real index at the new blobs, but only for paths whose staged
        version was the old commit's, so someone's own staged edit is kept."""
        staged = self._entries(("ls-files", "-s", "-z", "--", *paths))
        keep = [
            entry
            for path, entry in zip(paths, entries, strict=True)
            if staged.get(path, (None, None))[1] == old.get(path, (None, None))[1]
        ]
        if keep:
            listing = "".join(f"{e}\0" for e in keep).encode()
            self._exec(("update-index", "-z", "--index-info"), stdin=listing)

    def committed_paths(self, commit: str) -> list[str]:
        listing = self.run(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            "-z",
            "--root",
            commit,
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
