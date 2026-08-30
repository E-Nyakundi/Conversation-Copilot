import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import services
from .auth import token_required


def _quota_summary(quota):
    return {
        "max_sessions": quota.max_sessions,
        "sessions_used": quota.sessions_used,
        "sessions_remaining": quota.sessions_remaining(),
        "max_minutes": quota.max_minutes,
        "minutes_used": quota.minutes_used,
        "minutes_remaining": quota.minutes_remaining(),
        "is_active": quota.is_active,
    }


def _user_summary(user):
    data = {
        "id": user.id,
        "email": user.email,
        "is_staff": user.is_staff,
        "must_change_password": user.must_change_password,
    }
    quota = getattr(user, "quota", None)
    if quota is not None:
        data["quota"] = _quota_summary(quota)
    return data


@require_http_methods(["POST"])
@csrf_exempt
def login_view(request):
    """
    POST /api/auth/login/  body: {"email": "...", "password": "..."}
    -> {"token": "...", "user": {...}}

    Always issues a fresh token on successful login (see
    AuthToken.issue_for — one live token per user, so this also has the
    side effect of invalidating any session on another device; there is
    no concept of concurrent multi-device sessions in this app yet).
    """
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    email = body.get("email", "")
    password = body.get("password", "")
    if not email or not password:
        return JsonResponse({"error": "email and password are required"}, status=400)

    user = services.authenticate_credentials(email=email, password=password)
    if user is None:
        return JsonResponse({"error": "invalid credentials"}, status=401)

    token = services.issue_token_for(user)
    return JsonResponse({"token": token.key, "user": _user_summary(user)})


@require_http_methods(["POST"])
@csrf_exempt
@token_required
def change_password_view(request):
    """
    POST /api/auth/change-password/  body: {"new_password": "..."}

    Required after first login while must_change_password is set (the
    invite flow's temp password), but callable any time after that too
    — there's no separate "voluntary password change" endpoint, this is
    both.
    """
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    new_password = body.get("new_password", "")
    if not new_password or len(new_password) < 8:
        return JsonResponse(
            {"error": "new_password is required and must be at least 8 characters"},
            status=400,
        )

    services.change_password(user=request.user, new_password=new_password)
    # Token was rotated by change_password() — issue and return the new
    # one, since the one the client authenticated this request with is
    # now dead the moment this response is sent.
    new_token = request.user.auth_token
    return JsonResponse({"token": new_token.key, "user": _user_summary(request.user)})


@require_http_methods(["GET"])
@token_required
def me_view(request):
    """GET /api/auth/me/ -> current user + quota, for the Flutter app to
    check quota state (e.g. show remaining sessions) without a dedicated
    admin-only endpoint."""
    return JsonResponse(_user_summary(request.user))
