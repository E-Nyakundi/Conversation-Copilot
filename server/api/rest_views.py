import json

from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.auth import token_required
from conversation.models import Extraction, Session, Suggestion, Turn, UserProfile
from conversation.usage import finalize_session_usage


def _profile_summary(profile: UserProfile) -> dict:
    return {
        "name": profile.name,
        "role": profile.role,
        "bio": profile.bio,
        "is_populated": profile.is_populated(),
        "updated_at": profile.updated_at.isoformat(),
    }


def _session_summary(session: Session) -> dict:
    return {
        "id": str(session.id),
        "title": session.title,
        "mode": session.mode,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "turn_count": session.turns.count(),
    }


def _turn_summary(turn: Turn) -> dict:
    return {
        "id": str(turn.id),
        "speaker": turn.speaker,
        "text": turn.text,
        "started_at": turn.started_at.isoformat(),
        "ended_at": turn.ended_at.isoformat() if turn.ended_at else None,
        "confidence": turn.confidence,
    }


def _extraction_summary(extraction: Extraction) -> dict:
    return {
        "id": str(extraction.id),
        "type": extraction.type,
        "text": extraction.text,
        "confidence": extraction.confidence,
        "turn_id": str(extraction.turn_id) if extraction.turn_id else None,
        "created_at": extraction.created_at.isoformat(),
    }


def _suggestion_summary(suggestion: Suggestion) -> dict:
    return {
        "id": str(suggestion.id),
        "question": suggestion.question_text,
        "answer": suggestion.answer_text,
        "model_used": suggestion.model_used,
        "latency_ms": suggestion.latency_ms,
        "answer_level": suggestion.answer_level,
        # Extraction confidence, not model confidence — see the matching
        # note in audio/consumers.py._generate_suggestion.
        "extraction_confidence": (
            suggestion.extraction.confidence if suggestion.extraction_id else None
        ),
        "turn_id": str(suggestion.turn_id),
        "extraction_id": str(suggestion.extraction_id) if suggestion.extraction_id else None,
        "created_at": suggestion.created_at.isoformat(),
    }


def _get_owned_session(request, session_id):
    """
    Shared lookup for every /api/sessions/<id>/... view: multi-tenancy
    retrofit (2026-08-30) — a session that exists but belongs to a
    different tenant must 404, not 403, so its existence isn't
    disclosed to a user who doesn't own it. Raises Http404 either way.
    """
    try:
        return Session.objects.get(pk=session_id, owner=request.user)
    except (Session.DoesNotExist, ValueError):
        raise Http404("session not found")


@require_http_methods(["GET", "POST"])
@csrf_exempt
@token_required
def sessions_view(request):
    """
    GET  /api/sessions/  -> list the authenticated user's recent
                            sessions, newest first (multi-tenancy
                            retrofit, 2026-08-30 — previously listed
                            every session in the database)
    POST /api/sessions/  -> create a new session owned by the
                            authenticated user, gated on quota (returns
                            403 with a "reason" field — see
                            TenantQuota.can_start_session — if the
                            account is inactive or has exhausted its
                            session/minute cap)
                            body: {"title": "...", "mode": "..."} (both optional)
    """
    if request.method == "GET":
        sessions = Session.objects.filter(owner=request.user)[:100]
        return JsonResponse({"sessions": [_session_summary(s) for s in sessions]})

    quota = getattr(request.user, "quota", None)
    allowed, reason = (False, "account_inactive") if quota is None else quota.can_start_session()
    if not allowed:
        return JsonResponse({"error": "quota exceeded", "reason": reason}, status=403)

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        body = {}

    session = Session.objects.create(
        owner=request.user,
        title=body.get("title", ""),
        mode=body.get("mode", "general"),
    )
    return JsonResponse(_session_summary(session), status=201)


@require_http_methods(["GET"])
@token_required
def session_detail_view(request, session_id):
    """
    GET /api/sessions/<id>/  -> session details + all turns (transcript)
    """
    session = _get_owned_session(request, session_id)
    turns = session.turns.all()
    payload = _session_summary(session)
    payload["turns"] = [_turn_summary(t) for t in turns]
    return JsonResponse(payload)


@require_http_methods(["GET"])
@token_required
def session_extractions_view(request, session_id):
    """
    GET /api/sessions/<id>/extractions/ -> all extracted items for a
    session (questions, decisions, requests, objections, action items,
    topic changes), oldest first. Optional ?type=<type> filter.
    """
    session = _get_owned_session(request, session_id)
    extractions = session.extractions.all()
    type_filter = request.GET.get("type")
    if type_filter:
        extractions = extractions.filter(type=type_filter)

    return JsonResponse({"extractions": [_extraction_summary(e) for e in extractions]})


@require_http_methods(["GET"])
@token_required
def session_suggestions_view(request, session_id):
    """
    GET /api/sessions/<id>/suggestions/ -> all Phase 3 AI-generated
    suggestions for a session (one per detected question that got a
    local-LLM answer), oldest first.
    """
    session = _get_owned_session(request, session_id)
    suggestions = session.suggestions.select_related("extraction").all()
    return JsonResponse({"suggestions": [_suggestion_summary(s) for s in suggestions]})


@require_http_methods(["POST"])
@csrf_exempt
@token_required
def session_end_view(request, session_id):
    """POST /api/sessions/<id>/end/ — mark a session ended (e.g. app
    backgrounded/killed without a clean WebSocket close).

    Multi-tenancy retrofit (2026-08-30): also finalizes usage against
    the owner's quota via conversation.usage.finalize_session_usage —
    the same helper the WebSocket consumer's disconnect()/end_session
    path uses, so a session ended via either route is charged exactly
    once, the same way. Guarded by the same "only if ended_at was still
    None" check that already existed here, which is also what prevents
    double-charging if this endpoint is called more than once."""
    session = _get_owned_session(request, session_id)

    if session.ended_at is None:
        session.ended_at = timezone.now()
        session.save(update_fields=["ended_at"])
        finalize_session_usage(session)

    return JsonResponse(_session_summary(session))


@require_http_methods(["GET", "PUT"])
@csrf_exempt
@token_required
def user_profile_view(request):
    """
    Multi-tenancy retrofit (2026-08-30): UserProfile is no longer a
    global singleton (see conversation/models.py) — this now reads/
    writes the authenticated user's own profile row via
    UserProfile.get_for_user(request.user).

    GET /api/profile/ -> current profile (name/role/bio, always 200 even
                          if never set — an empty/unpopulated profile is a
                          normal state, not a 404).
    PUT /api/profile/ -> update the profile. body: {"name": "...",
                          "role": "...", "bio": "..."} — any subset;
                          fields not present in the body are left
                          unchanged (so a client editing just the bio
                          field doesn't need to resend name/role too).
    """
    profile = UserProfile.get_for_user(request.user)

    if request.method == "GET":
        return JsonResponse(_profile_summary(profile))

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    changed_fields = []
    for field in ("name", "role", "bio"):
        if field in body:
            value = body[field]
            if not isinstance(value, str):
                return JsonResponse({"error": f"{field} must be a string"}, status=400)
            setattr(profile, field, value)
            changed_fields.append(field)

    if changed_fields:
        profile.save()

    return JsonResponse(_profile_summary(profile))
