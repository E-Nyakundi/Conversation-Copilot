import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "copilot.settings")

# Must come after get_asgi_application() so Django apps are loaded first
django_asgi_app = get_asgi_application()

from copilot.routing import websocket_urlpatterns  # noqa: E402
from audio.warmup import run_warmup  # noqa: E402

# Load VAD/Whisper/local-LLM once, here, in the actual process that will
# serve connections — before Daphne binds its port. This used to be a
# separate `python manage.py warmup_models` step in docker-compose.yml
# that ran in its own process and exited, so Daphne started fresh with
# every model unloaded anyway and the first real WebSocket connection
# paid the full load cost (including Whisper's live huggingface.co
# revision check) regardless. See audio/warmup.py's docstring for the
# full explanation.
run_warmup()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
