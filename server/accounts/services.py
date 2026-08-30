"""
Invite-only account creation + auth helpers.

Kept as plain functions (not a DRF serializer/viewset layer) to match
the rest of this codebase's style — api/rest_views.py is plain Django
function views with manual JSON, not DRF.
"""

import secrets
import string

from django.db import transaction

from .models import AuthToken, TenantQuota, User

_TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def _generate_temp_password(length: int = 12) -> str:
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))


@transaction.atomic
def invite_user(
    *,
    email: str,
    created_by: User,
    max_sessions: int | None = None,
    max_minutes: int | None = None,
) -> tuple[User, str]:
    """
    Create a new tenant account (admin-only entry point — callers must
    check request.user.is_staff themselves; this function doesn't).

    Returns (user, temp_password). The temp password is generated here
    and returned exactly once — it is stored only as a hash (via
    set_password, same as any Django password) and cannot be recovered
    later, matching the design doc's "show it once at creation" note.
    """
    temp_password = _generate_temp_password()
    user = User.objects.create_user(
        email=email,
        password=temp_password,
        created_by=created_by,
        must_change_password=True,
    )
    TenantQuota.objects.create(
        user=user,
        max_sessions=max_sessions,
        max_minutes=max_minutes,
    )
    return user, temp_password


def authenticate_credentials(*, email: str, password: str) -> User | None:
    """Plain email+password check — used only by the login endpoint."""
    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return None
    if not user.check_password(password):
        return None
    if not user.is_active:
        return None
    return user


def issue_token_for(user: User) -> AuthToken:
    return AuthToken.issue_for(user)


def change_password(*, user: User, new_password: str) -> None:
    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    # Rotate the token on password change so a leaked old token (e.g. the
    # temp-password-era one) stops working immediately.
    AuthToken.issue_for(user)
