import json

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.auth import staff_required, token_required
from knowledge.documents import delete_document, ingest
from knowledge.models import Document
from knowledge.pdf_extract import PdfExtractError, extract_text


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
@staff_required  # Staff/admin path only — global, unscoped by owner. Left in
                 # place for whatever staff-only bulk/debug use it already had;
                 # tenant users go through my_documents_view below instead,
                 # which is owner-scoped and is what onboarding's PDF upload
                 # (build plan §15) actually calls. Do not point tenant-facing
                 # UI at this endpoint — it can see every tenant's documents.
def documents_view(request):
    """
    GET  /api/knowledge/documents/  -> list ALL uploaded documents (every
                                        tenant), newest first — staff only.
    POST /api/knowledge/documents/  -> upload a new text document, chunked
                                        + embedded + indexed synchronously
                                        (see knowledge/documents.py:ingest),
                                        unowned (owner=None) unless the
                                        caller passes one explicitly.
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


@require_http_methods(["GET", "POST"])
@csrf_exempt
@token_required
def my_documents_view(request):
    """
    Onboarding knowledge upload (2026-08-31, build plan §15): the
    tenant-facing counterpart to documents_view above — every operation
    here is scoped to request.user, so one tenant can never list, upload
    into, or (via my_document_detail_view below) read/delete another
    tenant's documents. This is the endpoint the Flutter onboarding step
    and any future "manage your knowledge base" UI should call — not
    documents_view, which is staff-only and unscoped on purpose.

    GET  /api/knowledge/my-documents/  -> list the current user's own
                                           documents, newest first.
    POST /api/knowledge/my-documents/  -> add a document to the current
                                           user's knowledge base. Two
                                           accepted bodies:
                                           - multipart/form-data with a
                                             `file` field: a PDF (CV,
                                             LinkedIn "Save to PDF"
                                             export, or GitHub profile
                                             page printed to PDF — see
                                             pdf_extract.py). Text is
                                             extracted server-side; an
                                             optional `title` form field
                                             is accepted too.
                                           - application/json:
                                             {"title": "...", "text": "..."}
                                             for plain text/notes, same
                                             shape as documents_view's
                                             POST body — kept for
                                             non-PDF uploads (pasted
                                             notes, etc.) rather than
                                             forcing everything through
                                             a PDF.
    """
    if request.method == "GET":
        documents = Document.objects.filter(owner=request.user).order_by("-created_at")[:100]
        return JsonResponse({"documents": [_document_summary(d) for d in documents]})

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        upload = request.FILES.get("file")
        if upload is None:
            return JsonResponse({"error": "'file' is required for a multipart upload"}, status=400)
        if not upload.name.lower().endswith(".pdf"):
            return JsonResponse({"error": "only .pdf files are accepted"}, status=400)

        try:
            text = extract_text(upload.read())
        except PdfExtractError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        title = (request.POST.get("title") or upload.name).strip()
        document = ingest(title=title, text=text, owner=request.user)
        return JsonResponse(_document_summary(document), status=201)

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "'text' is required"}, status=400)

    document = ingest(title=body.get("title", ""), text=text, owner=request.user)
    return JsonResponse(_document_summary(document), status=201)


@require_http_methods(["GET", "DELETE"])
@csrf_exempt
@token_required
def my_document_detail_view(request, document_id):
    """
    Owner-scoped counterpart to document_detail_view below — a document
    id that exists but belongs to a different tenant behaves exactly
    like "not found" (404), same posture as audio/consumers.py's
    session-resume ownership check, rather than leaking another
    tenant's existence/content to whoever guesses or reuses an id.

    GET    /api/knowledge/my-documents/<id>/  -> this document's full text + chunks
    DELETE /api/knowledge/my-documents/<id>/  -> remove it (DB rows + its
                                                  vectors in the shared FAISS
                                                  index, see documents.py:delete_document)
    """
    try:
        document = Document.objects.get(pk=document_id, owner=request.user)
    except (Document.DoesNotExist, ValueError):
        raise Http404("document not found")

    if request.method == "DELETE":
        delete_document(document)
        return JsonResponse({"deleted": True})

    payload = _document_summary(document)
    payload["chunks"] = [{"order": c.order, "text": c.text} for c in document.chunks.all()]
    return JsonResponse(payload)


@require_http_methods(["GET", "DELETE"])
@csrf_exempt
@staff_required  # see documents_view's comment above — same global-index limitation
def document_detail_view(request, document_id):
    """
    GET    /api/knowledge/documents/<id>/  -> a single document's full text + chunks
    DELETE /api/knowledge/documents/<id>/  -> remove a document (DB rows +
                                               its vectors in the FAISS index,
                                               see knowledge/documents.py:delete_document)
    Staff-only, unscoped by owner — see documents_view's comment.
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
