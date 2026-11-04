# Infrastructure options

Status: exploration; nothing provisioned · 2026-10-15

## Deployment models

| Model | Repository access | Strengths | Main costs | Recommendation |
| --- | --- | --- | --- | --- |
| Self-hosted, existing repo | Explicit filesystem adapter on the operator's host | Familiar adoption, data stays near tooling, limited services | Storage permissions, backups, external-writer coordination | Proposed first target |
| Hosted UI/API plus connector | Outbound authenticated connector next to each repo | Central service with repos kept in place | Enrollment, credential lifecycle, offline/retry semantics, capability reporting | Later if hosted product is desired |
| Hosted repo and client delivery | Managed metadata and package storage | Integrated publishing and delivery | Migration, large payload storage/egress, tenancy, download auth, recovery | Separate later decision |

Self-hosted does not mean every operation runs on Linux. Keep a Mac worker
optional and document its capabilities. Do not promise a working Linux
`makecatalogs` installation until it has been validated.

## First deployment proposal

- Build static browser assets and serve them with the API under one origin.
- Run one application instance with one repository writer. Run durable jobs in
  a separate process from the same versioned release when needed.
- Use an explicitly configured repository mount, read-only during discovery.
  A writable authoring checkout is preferred to mounting the live delivery tree.
- Keep SQLite, drafts, jobs, and audit on a local durable app-state volume. SQLite
  is a single-host proposal, not a database shared over NFS/SMB. Reassess PostgreSQL
  when multiple application instances or a hosted control plane are required.
- Keep uploads, worker staging, and repository packages outside the app image.
- Use an existing TLS ingress or a declared reverse-proxy configuration. Development
  binds only to loopback. The delivery server used by Munki clients remains its
  own configured service.
- Publish a Compose deployment once the app exists. Use explicit mount semantics,
  a non-root user, resource limits, health checks, and secrets supplied at runtime.
  Docker runs Linux containers even on a Mac; a Mac-native tool worker is separate.

No Redis, Kubernetes, search cluster, or object store is required for the first
single-host metadata explorer. Add them only for a measured requirement.

## Hosting comparison

| Hosting | Fit | Gate before selecting |
| --- | --- | --- |
| Existing VM + Docker Compose | Most direct route to an existing filesystem and durable local state | UID/GID, locking, backups, TLS, and network reachability |
| Managed container service, such as Azure Container Apps | Possible API/worker hosting with managed ingress and external persistence | Storage mounts and locking, minimum replicas, database, job lifecycle, identity and costs |
| Static host + edge functions | Useful for UI delivery or a connector-oriented control plane | Edge filesystem APIs are not a durable mount of the operator's repo; backend/connector and Mac jobs still required |
| Native Mac service | Direct access to Mac tools and local repo | Service packaging, identity/TLS, upgrades, isolation, and recoverability |

Azure Container Apps distinguishes temporary container/replica storage from
durable mounted Azure Files. That changes the state layout and needs a
filesystem-locking study; do not place SQLite on a shared network mount by
default. [Storage documentation](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts).

Cloudflare Workers' Node filesystem is a virtual filesystem with bundled
read-only content and temporary writable space. It is not a substitute for
mounting an existing persistent Munki repository. A hosted control plane could
use other storage services plus a connector, but that is a distinct architecture.
[Filesystem documentation](https://developers.cloudflare.com/workers/runtime-apis/nodejs/fs/).

## IaC contract

Docker Compose is the established deployment declaration for the proposed local
profile. For any selected cloud, Terraform must own compute, storage, network,
DNS, certificates, identities, OIDC registrations, assignments, secrets references,
monitoring, and backup schedules. Keep state encrypted and outside Git. Provider
credentials use the deployment platform's supported identity integration.

Review plans before deployment. Portal or ad hoc CLI changes are diagnosis or
temporary recovery only; reconcile them into code before calling the work done.
This planning pass intentionally has no placeholder deploy files that could be
mistaken for a tested infrastructure implementation.

## Operations and recovery

Back up three things: repository metadata and package payloads, durable app state,
and deployment configuration plus externally held secrets. Git is useful history
but does not back up ignored packages, draft state, audit, or the database.

Exercise restoration into a disposable environment before enabling production
writes. Define RPO/RTO and retention with the operator; they are currently open.
Show storage exhaustion, stale indexes, disconnected workers, stuck jobs, and
the deployed application version in operational diagnostics. Logs must omit
credentials, encoded profile payloads, and full scripts/metadata by default.

Upgrade through versioned images and explicit app-state migrations with backups.
Pin release images by digest in deployment configuration. Test rollback against
state migration compatibility instead of assuming an old image can open new state.

## Cost model to investigate

Do not choose a provider from an unverified headline price. Compare fixed
compute/idle capacity, app-state database, metadata volume, temporary upload space,
backup retention, package storage, package download egress, and Mac worker time.
First inventory only synthetic workloads: repo counts, metadata size, typical and
largest upload, concurrent editors, and expected job frequency. Get current pricing
after a deployment model and region are chosen. Existing client delivery costs
should not be attributed to the editor unless this app takes over delivery.

## First infrastructure spike

Prove a read-only scan on a disposable filesystem repo, app-state restoration,
external file-change detection, and capability reporting from a Mac worker.
For writable mode, prove external-writer coordination and failed-batch recovery.
Record a deployment choice only after those results; provision no cloud resources
as part of this planning task.
