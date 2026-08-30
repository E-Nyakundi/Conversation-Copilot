"""
Channels ASGI middleware that authenticates a WebSocket connection from
a `?token=<key>` query param, since there's no standard header mechanism
on a browser/Flutter WebSocket handshake the way there is for REST.

Wraps the URLRouter in copilot/asgi.py:

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": TokenAuthMiddleware(URLRouter(websocket_urlpatterns)),
    })

Consumers read the authenticated user from self.scope["user"] (None if
auth failed — AudioStreamConsumer.connect() is responsible for closing
the connection when that's the case; this middleware only resolves
identity, it doesn't enforce it, so non-tenant-scoped consumers like
PingConsumer are free to ignore scope["user"] entirely).
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware

from .models import AuthToken


@database_sync_to_async
def _resolve_token(key: str):
    if not key:
        return None
    try:
        token = AuthToken.objects.select_related("user").get(key=key)
    except AuthToken.DoesNotExist:
        return None
    if not token.user.is_active:
        return None
    return token.user


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token_key = (params.get("token") or [""])[0]
        scope["user"] = await _resolve_token(token_key)
        return await super().__call__(scope, receive, send)
