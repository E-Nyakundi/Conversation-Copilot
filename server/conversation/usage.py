"""
Single accounting path for "a session ended, charge it against the
owner's quota" — called from both AudioStreamConsumer (WS disconnect /
end_session) and api/rest_views.py's session_end_view, so a session
ended either way is counted exactly once, the same way.
"""

from django.utils import timezone


def compute_elapsed_minutes(session) -> float:
    if not session.started_at:
        return 0.0
    end = session.ended_at or timezone.now()
    return max(0.0, (end - session.started_at).total_seconds() / 60.0)


def finalize_session_usage(session) -> None:
    """
    Idempotency note: this must only be called once per session (guarded
    by callers checking `ended_at is None` before calling, same as the
    existing "mark ended" guards already in consumers.py/rest_views.py)
    — calling it twice would double-charge the same session against the
    owner's quota.
    """
    if session.owner_id is None:
        # Sessions created before the multi-tenancy retrofit (owner
        # nullable during migration) have nothing to charge — see
        # conversation/models.py Session.owner's docstring.
        return

    quota = getattr(session.owner, "quota", None)
    if quota is None:
        return

    minutes = compute_elapsed_minutes(session)
    quota.register_usage(minutes=minutes, session=session)
