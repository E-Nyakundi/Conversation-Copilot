from django.urls import re_path

from audio.consumers import AudioStreamConsumer, PingConsumer

# Phase 0: a single ping/echo consumer to validate the tunnel + WebSocket
# round trip.
#
# Phase 1: AudioStreamConsumer takes over the real work — raw PCM16 audio
# in, VAD/STT/speaker-labeled Turns out. session_id is optional: omit it to
# start a new Session, or include an existing one to resume/append to it.
websocket_urlpatterns = [
    re_path(r"^ws/ping/?$", PingConsumer.as_asgi()),
    re_path(
        r"^ws/audio/?(?:(?P<session_id>[0-9a-fA-F-]{36})/?)?$",
        AudioStreamConsumer.as_asgi(),
    ),
]
