from django.contrib import admin
from django.http import JsonResponse, HttpResponse
from django.urls import include, path
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # .../server
# Only ./server is bind-mounted into the container (see docker-compose.yml),
# so BASE_DIR.parent does NOT reach the repo root inside the container the
# way it would when running manage.py directly on the host. index.html is
# bind-mounted separately at this fixed path instead.
PROJECT_ROOT = Path("/frontend")
if not PROJECT_ROOT.exists():
    # Not running in the Docker container (e.g. `python manage.py runserver`
    # directly on the host) — fall back to the real repo root.
    PROJECT_ROOT = BASE_DIR.parent


def health(request):
    return JsonResponse({"status": "ok"})


def serve_html(base_dir, filename):
    def view(request):
        content = (base_dir / filename).read_text()
        return HttpResponse(content, content_type="text/html")
    return view


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("api/", include("api.urls")),
    path("api/knowledge/", include("knowledge.urls")),
    path("api/auth/", include("accounts.urls")),
    # Custom admin API (multi-tenancy retrofit, 2026-08-30) — deliberately
    # under /api/admin/ rather than /admin/api/ so it reads as part of the
    # api/ surface, with its own auth (accounts.auth.staff_required, not
    # Django's session-based admin.site auth above).
    path("api/admin/", include("accounts.admin_urls")),
    # No-tunnel frontend (auto-connects to ws://<host>/ws/audio/, see
    # SETUP_NO_TUNNEL.md). This was previously undefined, so
    # http://localhost:8000/ 404'd and there was no way to reach
    # index.html through the backend.
    path("", serve_html(PROJECT_ROOT, "index.html")),
    # Old tunnel-based manual-URL test pages — still useful for testing
    # from a phone over a real tunnel, kept as-is under /test/.
    path("test/audio/", serve_html(BASE_DIR, "audio_test.html")),
    path("test/latency/", serve_html(BASE_DIR, "latency_test.html")),
]