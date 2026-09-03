from django.urls import path

from . import external_transcribe, rest_views

urlpatterns = [
    path("sessions/", rest_views.sessions_view, name="sessions"),
    path("sessions/<str:session_id>/", rest_views.session_detail_view, name="session-detail"),
    path(
        "sessions/<str:session_id>/extractions/",
        rest_views.session_extractions_view,
        name="session-extractions",
    ),
    path(
        "sessions/<str:session_id>/suggestions/",
        rest_views.session_suggestions_view,
        name="session-suggestions",
    ),
    path("sessions/<str:session_id>/end/", rest_views.session_end_view, name="session-end"),
    path("profile/", rest_views.user_profile_view, name="user-profile"),
    # External, stateless transcription for sibling backends (Vontran) —
    # see external_transcribe.py's module docstring. Separate auth
    # (EXTERNAL_TRANSCRIBE_API_KEY), no session/turn/suggestion model
    # touched, added without changing anything above this line.
    path("external/transcribe/", external_transcribe.transcribe_view, name="external-transcribe"),
]
