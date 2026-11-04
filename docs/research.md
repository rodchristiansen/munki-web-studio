# Reference research

Reviewed 2026-10-15. These references inform proposals; they do not prove the
unimplemented web app meets their behavior.

## Existing projects

### MunkiStudio

Repository: <https://github.com/rodchristiansen/munkistudio>

Source revision inspected: `4f595a60e73e2a7ed0c3b3ef26036b92794631c9`.
Local design references also included screenshots of its package and manifest
editors. Those screenshots are not copied here: all design-study data is invented.
The source README's pre-alpha label is less informative than the implemented
navigation, services, and tests, so scope was checked against those too.

- `Sources/App/Navigation/SidebarSection.swift`: Dashboard, Packages, Manifests,
  Profiles, Catalogs, Dependencies, Build, Import, Promoter, Clean, Git.
- `Sources/App/Features/Packages/PackageDetailView.swift`: structured object editor.
- `Sources/App/Features/Manifests/ManifestDetailView.swift`: structured manifest work.
- `Sources/Core/Models/Pkginfo.swift`: optional values and unknown-field preservation.
- `Sources/Infra/FileIO/`: repository files, codecs, and atomic-write helpers.
- `Sources/Infra/Yaml/`: Munki scalar and round-trip concerns.
- `Sources/Infra/Make/`: official tool runners and process-bound operations.
- `Sources/PredicateBridge/`: Foundation predicate parsing, not portable browser code.
- `Tests/InfraTests/MunkiParityTests.swift`: parity testing as a useful model.

Use the three-pane workflow and domain boundaries as references. A browser port
also needs multi-user authorization, revision conflicts, durable server jobs, and
separate host capabilities. Do not assume SwiftUI/AppKit views or NSPredicate can
be reused directly on a Linux server. Verify licenses and notices before reusing
code; an independent implementation can still use common synthetic parity cases.

### MunkiWebAdmin2

Repository: <https://github.com/munki/mwa2>

Its README defines web editing of pkginfo and manifests as the core purpose and
keeps reporting separate. Its legacy dependency list describes that project's
history; it is not a dependency recommendation for this one. Upstream maintenance
continues separately from this rewrite.

## Primary documentation

| Source | What it informs |
| --- | --- |
| [Munki project](https://github.com/munki/munki) | Metadata and client-delivery context |
| [Supported pkginfo keys](https://github.com/munki/munki/wiki/Supported-Pkginfo-Keys) | Field semantics and compatibility inventory |
| [makecatalogs](https://github.com/munki/munki/wiki/makecatalogs) | Catalog generation from pkginfo; tool requirements and supported operations |
| [Python plistlib](https://docs.python.org/3/library/plistlib.html) | XML/binary plist parsing; typed values beyond ordinary JSON |
| [FastAPI](https://fastapi.tiangolo.com/) | API-first Python option and OpenAPI support |
| [Django authentication](https://docs.djangoproject.com/en/6.0/topics/auth/) | Integrated identity/session/permission primitives; OIDC still needs integration |
| [React](https://react.dev/learn) and [Vite](https://vite.dev/guide/) | Proposed component UI and static asset build |
| [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/) | Explicit host storage access and read-only mounting |
| [Compose services](https://docs.docker.com/reference/compose-file/services/) | Reproducible single-host deployment declaration |
| [Azure Container Apps storage](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts) | Ephemeral versus durable mounted storage |
| [Workers filesystem](https://developers.cloudflare.com/workers/runtime-apis/nodejs/fs/) | Virtual filesystem scope; existing repo access needs a separate design |
| [Browser File System API](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API) | User-mediated browser file access; does not replace trusted host tool execution |

No cost estimate, platform SLA, supported-framework version matrix, Linux Munki
tool parity, or YAML compatibility claim has been validated for this product.
Resolve these in bounded spikes and record results before selecting infrastructure.
