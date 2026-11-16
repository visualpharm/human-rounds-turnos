"""security_headers_middleware.py — the browser security headers, on every
response, as one ASGI boundary.

Mounted as the OUTERMOST user middleware in core/middleware_stack.py (the LAST add_middleware
call), outside the canonical-host redirect and the site-auth gate, so the 308
and the 401 those two answer themselves carry the headers too — a header added
around the routers only would miss exactly the responses an attacker can
reach on any path.

What is sent, and why each is safe for every installation:

  X-Content-Type-Options: nosniff     a referral upload or a JSON error is
                                      never re-interpreted as a script.
  X-Frame-Options: SAMEORIGIN         plus `frame-ancestors 'self'` for the
  Content-Security-Policy             browsers that read CSP: no other site
                                      can frame the panel login or a booking
                                      page (clickjacking). The app's own
                                      frames are all same-origin — the
                                      pre-consult interview side panel and
                                      the referral viewer — so they keep
                                      working.
  Referrer-Policy:                    a link out of the site never carries a
    strict-origin-when-cross-origin   path, and paths here hold tokens
                                      (`/appointment/confirm/<token>`).
  Strict-Transport-Security           ONLY when the request arrived over
                                      https: pinning https on a plain-http
                                      install (localhost, a LAN box) would
                                      lock its users out. One year, this
                                      host only — no includeSubDomains, no
                                      preload, because the engine cannot
                                      know what else lives under the
                                      installation's domain.

A route that already sets one of these keeps its own value: the custom logo
and the referral viewer send a stricter Content-Security-Policy
(`default-src 'none'; sandbox`), and a second CSP header would be a second
policy, not a replacement.

Pure ASGI on purpose (no BaseHTTPMiddleware): it only rewrites the
`http.response.start` message, so streamed static files and the body are
passed through untouched.
"""

_ALWAYS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"content-security-policy", b"frame-ancestors 'self'"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)
_HSTS = (b"strict-transport-security", b"max-age=31536000")


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wanted = _ALWAYS + ((_HSTS,) if _arrived_over_https(scope) else ())

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {bytes(name).lower() for name, _ in headers}
                headers.extend(pair for pair in wanted if pair[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _arrived_over_https(scope):
    """The proxy's `X-Forwarded-Proto` when there is one (first hop, the same
    signal `admin_auth._secure_cookie_from` trusts for the cookie's `Secure`
    flag), the socket's own scheme otherwise (TLS terminated by the server
    itself)."""
    for name, value in scope.get("headers") or ():
        if bytes(name).lower() == b"x-forwarded-proto":
            first = bytes(value).split(b",")[0].strip().lower()
            return first == b"https"
    return scope.get("scheme") == "https"
