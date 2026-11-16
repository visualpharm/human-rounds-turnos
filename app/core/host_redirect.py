"""Canonical-host redirect for the Pinamar Turnos deployment."""

CANONICAL_ORIGIN = "https://pinamar.humanrounds.org"
OLD_HOSTS = {"sana-turnos.brieflysites.com"}

# Vanity domains with no path structure of their own: always land on one
# fixed page on the canonical origin, regardless of the requested path.
FIXED_TARGETS = {"salud.brieflysites.com": CANONICAL_ORIGIN + "/datos"}


def target(host_header, path):
    host = str(host_header or "").split(":", 1)[0].strip().lower()
    if host in FIXED_TARGETS:
        return FIXED_TARGETS[host]
    if host not in OLD_HOSTS:
        return None
    suffix = str(path or "/")
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return CANONICAL_ORIGIN + suffix
