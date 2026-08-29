"""
core/security_headers.py
------------------------
Baseline HTTP security headers (F18).

Adds hardening headers to every response:
  - X-Content-Type-Options: nosniff      — block MIME sniffing
  - X-Frame-Options: DENY                — legacy clickjacking protection
  - Content-Security-Policy: frame-ancestors 'none'
                                         — modern clickjacking protection; kept
                                           minimal so it does not break /docs
  - Referrer-Policy: no-referrer         — don't leak URLs to third parties
  - Strict-Transport-Security            — HTTPS-only; added in production only,
                                           where the site is served over TLS

Toggle via settings.security_headers_enabled.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.config import settings

_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}

_HSTS_VALUE = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if not settings.security_headers_enabled:
            return response
        for key, value in _STATIC_HEADERS.items():
            response.headers.setdefault(key, value)
        # HSTS only in production — pointless (and ignored) over plain-http dev.
        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", _HSTS_VALUE)
        return response
