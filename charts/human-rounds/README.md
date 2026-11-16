# Human Rounds Helm chart

This chart deploys the Human Rounds application with the same container
contract as the repository's Docker deployment: HTTP on port `7700`, liveness
at `/health`, readiness at `/ready`, and durable runtime state at `/app/data`.

## Required operator input

The default database path is deliberately external. Before an install, create
a Kubernetes Secret named `human-rounds-database` containing a `DATABASE_URL`
key, or set `database.existingSecret` and `database.existingSecretKey` to an
existing Secret and key. The chart never creates a database password or embeds
a connection string in values.

`persistence.enabled` defaults to true and creates a PVC at `/app/data`.
Use `persistence.existingClaim` to mount a pre-provisioned claim. Disabling
persistence switches to `emptyDir`, which is only appropriate for disposable
development because uploads, install-owned packs, and runtime configuration
will disappear on replacement.

The chart also mounts an ephemeral writable `emptyDir` at `/app/logs`. The
container runs as UID/GID 1000 while the image tree is root-owned; isolating
logs this way preserves the non-root runtime without treating logs as durable
application state.

## Image selection

The default repository is `ghcr.io/visualpharm/sana-turnos` and the default
tag is `latest`. Set `image.tag` to a released version for a mutable-tag
install. For an immutable install, set `image.digest` to a complete digest
such as `sha256:<64 hexadecimal characters>`; the chart then renders
`repository@digest` and ignores `image.tag`.

The repository's release workflow is designed to publish GHCR artifacts, but
no actual image publication is claimed by this chart. Verify the selected tag
or digest exists and comes from an approved release before installing.

## Application configuration

Use `applicationEnv` for arbitrary non-secret Human Rounds environment values,
for example time-zone or serving-mode settings. It is a pass-through map, not
a second copy of the application's environment-variable documentation. Keep
credentials in Kubernetes Secrets and integrate them through a reviewed
deployment extension; do not add credentials to `values.yaml`.

`DATABASE_URL` and `PORT` are reserved and rejected in `applicationEnv` at
render time. `DATABASE_URL` must keep coming from `database.existingSecret`;
the application container always listens on port `7700`.

`service.port` is the cluster-facing Service port and may be changed without
changing the application port. The Service targets the named `http` container
port, and both probes use that same named port.

## PostgreSQL boundary

`postgresql.enabled` is false by default. It is only a deliberately explicit
scaffold: enabling it fails Helm rendering rather than pretending that the
chart provides database backups, credentials, restore tests, upgrades, or high
availability. Use an externally managed PostgreSQL deployment and the
`DATABASE_URL` Secret until those operational guarantees are implemented and
verified.

## Proof boundary

The chart was exercised on 2026-09-03 with Helm 4.2.4, kind 0.33.0 and
Kubernetes 1.37.0. The disposable cluster used a local image built from the
same checkout and a separate PostgreSQL 16 fixture, supplied through the
documented `human-rounds-database` Secret. `helm lint`, `helm install --wait`,
the `/health`, `/ready` and `/setup` probes, and a `kind-v1` to `kind-v2`
`helm upgrade --wait` all passed. After the rollout every application pod
referenced only `human-rounds:kind-v2`; the cluster was then deleted.

That proof validates the chart's install and rolling-update mechanics. It is
not evidence that the repository's GHCR workflow has published a release, and
it does not turn the deliberately external PostgreSQL boundary into a bundled
database promise. The repository test `tests/test_helm_chart_contract.py`
continues to assert the source-level deployment contract, including the
writable-log and reserved-environment guards.
