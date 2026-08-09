from django.urls import path

from . import rest_views

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
]
