import json

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.auth import staff_required
from knowledge.documents import delete_document, ingest
from knowledge.models import Document


def _document_summary(document: Document) -> dict:
    return {
        "id": str(document.id),
        "title": document.title,
        "created_at": document.created_at.isoformat(),
        "chunk_count": document.chunks.count(),
        "text_length": len(document.text),
    }


@require_http_methods(["GET", "POST"])
@csrf_exempt
@staff_required  # KNOWN LIMITATION, see knowledge/models.py Document docstring:
                 # the FAISS index backing this is still global/unscoped, so this
                 # endpoint is restricted to staff (the deployment owner) only
                 # until per-tenant retrieval is built. Do not relax this to
                 # token_required without also fixing the vector store.
def documents_view(request):
    """
    GET  /api/knowledge/documents/  -> list uploaded documents, newest first
    POST /api/knowledge/documents/  -> upload a new text document, chunked
                                        + embedded + indexed synchronously
                                        (see knowledge/documents.py:ingest)
                                        body: {"title": "...", "text": "..."}
                                        ("text" required, "title" optional)
    """
    if request.method == "GET":
        documents = Document.objects.all().order_by("-created_at")[:100]
        return JsonResponse({"documents": [_document_summary(d) for d in documents]})

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "'text' is required"}, status=400)

    document = ingest(title=body.get("title", ""), text=text)
    return JsonResponse(_document_summary(document), status=201)


@require_http_methods(["GET", "DELETE"])
@csrf_exempt
@staff_required  # see documents_view's comment above — same global-index limitation
def document_detail_view(request, document_id):
    """
    GET    /api/knowledge/documents/<id>/  -> a single document's full text + chunks
    DELETE /api/knowledge/documents/<id>/  -> remove a document (DB rows +
                                               its vectors in the FAISS index,
                                               see knowledge/documents.py:delete_document)
    """
    try:
        document = Document.objects.get(pk=document_id)
    except (Document.DoesNotExist, ValueError):
        raise Http404("document not found")

    if request.method == "DELETE":
        delete_document(document)
        return JsonResponse({"deleted": True})

    payload = _document_summary(document)
    payload["chunks"] = [{"order": c.order, "text": c.text} for c in document.chunks.all()]
    return JsonResponse(payload)
