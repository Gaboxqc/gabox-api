"""Response security headers.

These matter less for a JSON API than for an HTML app, but two of them are load
bearing here: `nosniff` stops a browser from reinterpreting a JSON response as
something executable, and `no-store` keeps authenticated responses — which carry
the username and CSRF token — out of caches.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Applied to every response.
_BASE_HEADERS = {
    # Never let a browser second-guess the declared content type.
    "X-Content-Type-Options": "nosniff",
    # The API has no UI worth framing, and /docs should not be embeddable either.
    "X-Frame-Options": "DENY",
    # Send the origin but not the path to other sites; API paths can name resources.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Nothing here needs a camera, microphone or location.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Paths whose responses must never be cached, by the browser or anything between.
_NO_STORE_PREFIXES = ("/auth",)

# Two years. No `preload`: that is submitted to a browser-maintained list and is
# painful to reverse, which is a poor fit for a personal project.
_HSTS = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool = True) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        for header, value in _BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        # Browsers ignore HSTS over plain http, but sending it only on https
        # keeps local development output honest.
        if self.hsts and request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", _HSTS)

        if request.url.path.startswith(_NO_STORE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"

        return response
