"""
Owner/staff-only admin API — backs the custom admin UI, kept in its own
module (and its own URL prefix, /api/admin/) rather than mixed into
accounts/views.py so tenant-facing and admin-facing endpoints can never
be confused for each other at a glance.

Every view here is gated with @staff_required (accounts/auth.py), which
re-checks token auth itself, so there's no path where an unauthenticated
or non-staff request reaches these handlers.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import services
from .auth import staff_required
from .models import TenantQuota, UsageEvent, User
from .views import _quota_summary


def _usage_event_summary(event):
    return {
        "id": str(event.id),
        "session_id": str(event.session_id) if event.session_id else None,
        "minutes": event.minutes,
        "note": event.note,
        "created_at": event.created_at.isoformat(),
    }


def _admin_user_summary(user, *, include_events=False):
    quota = getattr(user, "quota", None)
    data = {
        "id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat(),
        "quota": _quota_summary(quota) if quota else None,
    }
    if include_events:
        events = user.usage_events.all()[:50]
        data["usage_events"] = [_usage_event_summary(e) for e in events]
    return data


@require_http_methods(["GET", "POST"])
@csrf_exempt
@staff_required
def admin_users_view(request):
    """
    GET  /api/admin/users/  -> list all tenants + quota summary
    POST /api/admin/users/  -> create an invite
         body: {"email": "...", "max_sessions": <int|null>, "max_minutes": <int|null>}
         -> {"user": {...}, "temp_password": "..."}  (shown exactly once)
    """
    if request.method == "GET":
        users = User.objects.select_related("quota").order_by("-created_at")
        return JsonResponse({"users": [_admin_user_summary(u) for u in users]})

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    email = body.get("email", "").strip()
    if not email:
        return JsonResponse({"error": "email is required"}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"error": "a user with that email already exists"}, status=409)

    max_sessions = body.get("max_sessions")
    max_minutes = body.get("max_minutes")
    for field_name, value in (("max_sessions", max_sessions), ("max_minutes", max_minutes)):
        if value is not None and not isinstance(value, int):
            return JsonResponse({"error": f"{field_name} must be an integer or null"}, status=400)

    user, temp_password = services.invite_user(
        email=email,
        created_by=request.user,
        max_sessions=max_sessions,
        max_minutes=max_minutes,
    )
    return JsonResponse(
        {"user": _admin_user_summary(user), "temp_password": temp_password},
        status=201,
    )


@require_http_methods(["GET"])
@staff_required
def admin_user_detail_view(request, user_id):
    """GET /api/admin/users/<id>/ -> full detail incl. recent usage events."""
    try:
        user = User.objects.select_related("quota").get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        return JsonResponse({"error": "user not found"}, status=404)

    return JsonResponse(_admin_user_summary(user, include_events=True))


@require_http_methods(["PATCH"])
@csrf_exempt
@staff_required
def admin_user_quota_view(request, user_id):
    """
    PATCH /api/admin/users/<id>/quota/
    body: {"sessions_delta": <int>, "minutes_delta": <int>}
      -> adds to (or, if negative, subtracts from) the user's max caps.
    Also accepts {"max_sessions": <int|null>, "max_minutes": <int|null>}
      -> sets the caps to an absolute value instead (null = unlimited).
    A request may use either style but not mix them for the same field.
    """
    try:
        user = User.objects.select_related("quota").get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        return JsonResponse({"error": "user not found"}, status=404)

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    quota: TenantQuota = user.quota

    has_absolute = "max_sessions" in body or "max_minutes" in body
    has_delta = "sessions_delta" in body or "minutes_delta" in body

    if has_absolute and has_delta:
        return JsonResponse(
            {"error": "use either absolute (max_sessions/max_minutes) or delta "
                      "(sessions_delta/minutes_delta) fields, not both"},
            status=400,
        )

    if has_absolute:
        if "max_sessions" in body:
            quota.max_sessions = body["max_sessions"]
        if "max_minutes" in body:
            quota.max_minutes = body["max_minutes"]
        quota.save(update_fields=["max_sessions", "max_minutes", "updated_at"])
        UsageEvent.objects.create(
            user=user,
            session=None,
            minutes=0,
            note=f"admin set max_sessions={quota.max_sessions}, max_minutes={quota.max_minutes}",
        )
    elif has_delta:
        quota.adjust(
            sessions_delta=int(body.get("sessions_delta", 0)),
            minutes_delta=int(body.get("minutes_delta", 0)),
        )
    else:
        return JsonResponse({"error": "no quota fields provided"}, status=400)

    quota.refresh_from_db()
    return JsonResponse(_quota_summary(quota))


@require_http_methods(["POST"])
@csrf_exempt
@staff_required
def admin_user_revoke_view(request, user_id):
    """POST /api/admin/users/<id>/revoke/ -> quota.is_active = False.
    Distinct from deactivating the Django account itself (user.is_active)
    so a revoked tenant still shows up correctly in the admin list rather
    than needing a separate "disabled accounts" view."""
    try:
        user = User.objects.select_related("quota").get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        return JsonResponse({"error": "user not found"}, status=404)

    user.quota.is_active = False
    user.quota.save(update_fields=["is_active", "updated_at"])
    return JsonResponse(_quota_summary(user.quota))


@require_http_methods(["POST"])
@csrf_exempt
@staff_required
def admin_user_reactivate_view(request, user_id):
    """POST /api/admin/users/<id>/reactivate/ -> quota.is_active = True."""
    try:
        user = User.objects.select_related("quota").get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        return JsonResponse({"error": "user not found"}, status=404)

    user.quota.is_active = True
    user.quota.save(update_fields=["is_active", "updated_at"])
    return JsonResponse(_quota_summary(user.quota))
