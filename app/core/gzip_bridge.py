"""gzip_bridge.py — gzip for the text responses the app builds.

Named `gzip_bridge`, not `compression`: Python 3.13+ ships a stdlib
`compression` package (used internally by `gzip`/`bz2`/`lzma`), and a local
module of that name shadows it — `import gzip` inside this very file broke
with `ModuleNotFoundError: No module named 'compression._common'` before this
got renamed away from the collision.

Cloudflare already compresses what reaches the patient (`content-encoding:
br` shows up in prod), so this does NOT change what the browser downloads.
What it fixes is the origin→Cloudflare leg, which travelled uncompressed:
listing-page HTML runs around 95 kB and that leg gets paid on EVERY visit,
because the HTML is `cf-cache-status: DYNAMIC` (never cached, unlike the
.js/.css files, which are now immutable).

Deliberate:
- Only `Accept-Encoding: gzip`. No brotli: compressing with brotli in Python
  is quite a bit more CPU-expensive, and what reaches the patient is already
  brotli'd by Cloudflare — here it's enough to avoid sending plain text.
- `level=5`: the gzip curve flattens out quickly; 9 spends a lot more CPU on
  a small server to gain little.
- `_MIN_SIZE` byte floor: compressing a short response can make it bigger,
  and it also saves the work on the wizard's small JSON payloads.
- `Vary: Accept-Encoding` whenever compression happens, so no intermediate
  cache serves the gzipped body to a client that didn't ask for it.
"""

import gzip

_MIN_SIZE = 1024
_LEVEL = 5


def accepts_gzip(request_like):
    ae = ""
    try:
        ae = request_like.headers.get("Accept-Encoding") or ""
    except Exception:  # noqa: BLE001 — missing headers means no compression
        return False
    return "gzip" in ae.lower()


def body_for(request_like, body):
    """Returns `(body, encoding_or_None)`. The caller sends the
    `Content-Encoding` header only if an encoding comes back."""
    if not body or len(body) < _MIN_SIZE or not accepts_gzip(request_like):
        return body, None
    try:
        compressed = gzip.compress(body, _LEVEL)
    except Exception:  # noqa: BLE001 — when in doubt, plain text
        return body, None
    if len(compressed) >= len(body):
        return body, None
    return compressed, "gzip"
