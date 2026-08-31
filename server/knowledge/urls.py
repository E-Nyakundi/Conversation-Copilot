from django.urls import path

from . import rest_views

urlpatterns = [
    path("documents/", rest_views.documents_view, name="knowledge-documents"),
    path("documents/<str:document_id>/", rest_views.document_detail_view, name="knowledge-document-detail"),
    # Onboarding knowledge upload (2026-08-31, build plan §15): owner-scoped
    # tenant-facing endpoints — see rest_views.my_documents_view's docstring
    # for why these are separate from documents_view/document_detail_view
    # above rather than a flag on the same views.
    path("my-documents/", rest_views.my_documents_view, name="knowledge-my-documents"),
    path("my-documents/<str:document_id>/", rest_views.my_document_detail_view, name="knowledge-my-document-detail"),
]
