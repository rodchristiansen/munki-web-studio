"""Response shapes. These define the OpenAPI schema the browser client is built from."""

from typing import Any

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    message: str
    line: int | None = None
    column: int | None = None


class ObjectSummary(BaseModel):
    path: str = Field(description="Relative path; the object's identity in the repository")
    kind: str
    format: str | None
    status: str = Field(description="ok, or invalid when the file could not be read")
    revision: str
    name: str | None
    version: str | None
    display_name: str | None
    category: str | None
    catalogs: list[str]
    diagnostic: Diagnostic | None = None


class PackageVersion(BaseModel):
    version: str | None
    path: str
    catalogs: list[str]
    status: str


class Package(BaseModel):
    name: str
    display_name: str | None
    category: str | None
    versions: list[PackageVersion]


class PackagePage(BaseModel):
    items: list[Package]
    total: int
    limit: int
    offset: int


class ObjectPage(BaseModel):
    items: list[ObjectSummary]
    total: int
    limit: int
    offset: int


class ObjectDetail(ObjectSummary):
    data: Any = Field(
        default=None,
        description="The file's contents as tagged JSON (see tagged.py); null when invalid",
    )


class CatalogSummary(BaseModel):
    name: str
    items: int


class ScanSummary(BaseModel):
    started_at: str
    finished_at: str
    files: int


class RepositoryInfo(BaseModel):
    id: str
    name: str
    writable: bool
    last_scan: ScanSummary | None


class ReferenceInfo(BaseModel):
    source: str = Field(description="Path of the object that holds the reference")
    field: str
    target: str
    condition: str | None


class ValidationIssue(BaseModel):
    severity: str = Field(description="error or warning")
    code: str
    path: str
    message: str
    field: str | None = None
    target: str | None = None


class ValidationReport(BaseModel):
    errors: int
    warnings: int
    issues: list[ValidationIssue]


class ConditionInfo(BaseModel):
    text: str
    rule: str | None = Field(
        description="The condition as a readable sentence; null when it does not parse"
    )
    error: str | None
    column: int | None
    facts: list[str]
    unknown_facts: list[str]
