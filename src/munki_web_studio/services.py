"""What every front door (API, MCP, command line) works against: the configured
repositories and their index."""

from dataclasses import dataclass

from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index


class NoRepositoryError(LookupError):
    pass


@dataclass
class Services:
    settings: Settings
    index: Index | None = None

    def repository(self, repo_id: str | None = None) -> Repository:
        """The named repository, or the first one when no name is given."""
        if repo_id:
            try:
                return self.settings.repository(repo_id)
            except KeyError:
                raise NoRepositoryError(f"No repository {repo_id!r}") from None
        if not self.settings.repositories:
            raise NoRepositoryError("No repository is configured")
        return self.settings.repositories[0]

    def require_index(self) -> Index:
        if self.index is None:
            raise NoRepositoryError("The repository index is not open yet")
        return self.index
