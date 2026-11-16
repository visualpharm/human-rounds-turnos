"""services/ — process-level service layer for the ASGI composition root
(Tier 4 composition extraction, docs/roadmap/tier4-replatform-design.md).

A service here owns transport behavior the composition root wires together
but must not implement itself. The ASGI runtime is native; the remaining
services are transport-neutral HTTP/static adapters shared with the dark
stdlib rollback topology.

Dependency direction (enforced by tests/test_domain_dependency_direction.py):
a service may import core infra (core.*, stdlib, third-party transport
libraries) and NOTHING above it — never `app`, `server`, `routers`, nor any
domain/screen/module package. `routers/` and `app.py` are this package's
consumers, not its dependencies.
"""
