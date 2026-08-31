"""
Minimal, hand-rolled CORS handling — added 2026-08-30 alongside the admin
UI, which is a separate origin (its own Next.js dev server or deployed
host) making fetch() calls to this Django server's /api/ routes from the
browser.

Not django-cors-headers: this codebase has stayed dependency-light
throughout (see accounts/auth.py's docstring on why token auth is
hand-rolled rather than pulling in DRF), and the actual need here is
narrow enough not to justify a new package — allow a configured set of
origins to call /api/*, nothing more.

ADMIN_UI_ORIGINS (settings.py) lists the exact origins allowed to make
cross-origin requests here — e.g. "http://localhost:3000" for local
admin UI dev, plus whatever host you deploy it to. Wildcard "*" is
deliberately not supported: this API issues bearer tokens, and a
wildcard Origin with credentialed requests is exactly the CORS
misconfiguration that defeats the purpose of scoping this at all.
"""

from django.conf import settings
from django.http import HttpResponseNoContent


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get("HTTP_ORIGIN")
        allowed = origin is not None and origin in getattr(settings, "ADMIN_UI_ORIGINS", [])

        if request.method == "OPTIONS" and allowed:
            # Preflight: browsers send this before the real request for
            # any cross-origin call carrying a custom header (our
            # Authorization: Bearer header qualifies) — respond here
            # rather than letting it fall through to the real view, which
            # would otherwise 404/405 it.
            response = HttpResponseNoContent()
        else:
            response = self.get_response(request)

        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response["Vary"] = "Origin"

        return response
