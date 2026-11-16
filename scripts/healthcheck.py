"""healthcheck.py — the image's HEALTHCHECK: GET /health on the address the app
really listens on (Dockerfile; docs/architecture/deploy.md).

`HOST` decides the family. Coolify binds `0.0.0.0` (IPv4 only), Railway binds
`::` for its private network and there the app does not answer on 127.0.0.1
(municipality's platform team, 2026-10-19). So a wildcard is probed on the
loopback of its own family first and then on the other one; an explicit
address is probed as it is. urllib, because the image has neither curl nor
wget. Exit 0 = healthy, 1 = not.
"""
import os
import sys
import urllib.request


def candidates(host):
    host = (host or "").strip()
    if host in ("", "0.0.0.0"):
        return ["127.0.0.1", "[::1]"]
    if host == "::":
        return ["[::1]", "127.0.0.1"]
    return ["[" + host + "]" if ":" in host and not host.startswith("[") else host]


def main():
    port = os.environ.get("PORT", "7700")
    for host in candidates(os.environ.get("HOST")):
        try:
            urllib.request.urlopen("http://%s:%s/health" % (host, port), timeout=4)
            return 0
        except Exception:
            continue
    return 1


if __name__ == "__main__":
    sys.exit(main())
