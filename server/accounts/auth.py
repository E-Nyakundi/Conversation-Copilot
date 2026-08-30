"""
Bearer-token authentication for plain Django views (no DRF in this
project — see accounts/services.py's docstring for why).

Usage:
    @require_http_methods(["GET"])
    @token_required
    def my_view(request):
        request.user  # set to the authenticated accounts.User
        ...
"""

import functools

from django.http import JsonResponse

from .models import AuthToken


def get_user_from_request(request):
    """
    Returns the authenticated User for a request carrying
    `Authorization: Bearer <token>`, or None if missing/invalid. Shared
    by both the @token_required decorator and the admin-only variant so
    there's exactly one place that parses the header.
    """
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return None
    key = header[len("Bearer "):].strip()
    if not key:
        return None
    try:
        token = AuthToken.objects.select_related("user").get(key=key)
    except AuthToken.DoesNotExist:
        return None
    if not token.user.is_active:
        return None
    return token.user


def token_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "authentication required"}, status=401)
        request.user = user
        return view_func(request, *args, **kwargs)

    return wrapper


def staff_required(view_func):
    """
    For the admin API only. Layered on top of token_required's job (it
    re-checks auth itself so it can be used standalone) plus an
    is_staff gate — kept as a separate decorator rather than a flag on
    token_required so a tenant-facing view can never accidentally be
    left open to staff-only logic by a misplaced argument.
    """

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "authentication required"}, status=401)
        if not user.is_staff:
            return JsonResponse({"error": "admin access required"}, status=403)
        request.user = user
        return view_func(request, *args, **kwargs)

    return wrapper
