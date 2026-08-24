from django.urls import path

from . import rest_views

urlpatterns = [
    path("documents/", rest_views.documents_view, name="knowledge-documents"),
    path("documents/<str:document_id>/", rest_views.document_detail_view, name="knowledge-document-detail"),
]
