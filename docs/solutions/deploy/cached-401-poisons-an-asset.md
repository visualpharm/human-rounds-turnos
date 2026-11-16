# A 401 served with `immutable` poisons that asset for a year

**2026-07-28, prod.** the product owner's Chrome kept showing "Authentication Failed" on
`https://sana-turnos.brieflysites.com/admin-cola.js?v=31` while the shared
Basic Auth credentials were unchanged and correct.

## The symptom that misleads

`curl -u` with the shared credentials returned **200 on every probe** (12/12, page and asset),
wrong credentials returned 401, no credentials returned 401. The gate was
working perfectly. Coolify's runtime env still held
the same `TURNOS_BASIC_AUTH_USER` / `TURNOS_BASIC_AUTH_PASS`. Nothing on the
server side was broken — so "basic auth is broken" was **only true inside one
browser**, and only for specific URLs.

## The cause

`services.legacy_http.LegacyHttpMixin.end_headers` stamped cache headers by looking at the
**path extension only**, never at the status code:

```python
if p.endswith(self._INMUTABLE_EXT):
    if versionado:
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
```

`site_auth.py`'s 401 goes out through that same `end_headers`. So a request for
`/admin-cola.js?v=31` **without credentials** was answered:

```
HTTP/2 401
cache-control: public, max-age=31536000, immutable
www-authenticate: Basic realm="Sana Turnos"
```

A 401 is cacheable when the response says so explicitly (RFC 9111 §3), and the
browser's cache key does **not** include the `Authorization` header. So the
browser stored that 401 for a year and served it from cache on every later
request for that exact URL — credentials attached or not. The version query
can't save you: `?v=31` is the poisoned key.

Cloudflare showed `cf-cache-status: BYPASS`/`MISS` on the 401, so the edge was
not the culprit here — but the same header would let it cache one too.

## The fix

Track the status being sent and never cache an error:

```python
def send_response_only(self, code, message=None):
    self._status_code = code          # BaseHTTPRequestHandler stores it nowhere
    super().send_response_only(code, message)

def end_headers(self):
    code = getattr(self, "_status_code", 200)
    if code >= 400:
        self.send_header("Cache-Control", "no-store")
    elif p.endswith(self._INMUTABLE_EXT):
        ...
```

Guard: `test/test_cache_headers.py` (every 4xx/5xx on every asset shape gets
`no-store`; 200 and 304 keep the immutable header).

The same rule applies to redirects into content-hashed assets. A legacy
`/admin-cola.js?v=31` alias may redirect to the current
`/static/admin-queue.<hash>.js`, but the 301 itself must be `no-store` and must
discard the old query. If that redirect is immutable, a later deploy changes
the target hash while the browser keeps redirecting to the old hash for a year;
the correctly fail-closed static handler then returns 404. The immutable cache
belongs only on the byte-verified final asset response, never on an alias.

## Clearing an already-poisoned browser

Shipping the fix does nothing for a client that already cached the 401 — the
entry is valid for a year and `immutable` tells the browser not to revalidate.
Either the person does one hard reload (Cmd+Shift+R, which bypasses the cache
for the document and its subresources) or the asset's `?v=` is bumped, which
mints a new cache key.

## The general rule

Any header policy keyed on the URL must also read the status code. Error
responses and redirects to content-hashed assets are `no-store`, always. Only
the exact current fingerprint URL is immutable; caching an intermediate
response can outlive the content identity it selected.
