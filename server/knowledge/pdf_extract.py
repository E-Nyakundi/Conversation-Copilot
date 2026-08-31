"""
Onboarding knowledge upload (2026-08-31, build plan §15): pulls plain
text out of a PDF a user uploads during onboarding — a CV export, a
LinkedIn "Save to PDF" profile export, or a GitHub profile page printed
to PDF are the three cases this was actually built for (see
rest_views.py's upload endpoint and the Flutter onboarding step). No PDF
structure/layout is preserved deliberately — same "plain text is enough"
scope call already made for the original text-upload path (see
Document's docstring) — the goal is a document that can be chunked and
embedded like any other, not a faithful re-render of the source PDF.

Deferred pypdf import, same "don't hard-require a heavy dependency just
to boot Django" pattern as knowledge/embeddings.py and stt/engine.py.
"""

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10MB — generous for a CV/profile export, not for arbitrary uploads


class PdfExtractError(Exception):
    """Raised for a PDF that can't be read at all (corrupt, encrypted, not actually a PDF)."""


def extract_text(pdf_bytes: bytes) -> str:
    """
    Returns the concatenated text of every page, in order, separated by
    blank lines (so chunk_text's paragraph-aware splitting in
    knowledge/chunking.py treats a page break the same as a paragraph
    break — a reasonable proxy since CVs/profile exports are usually
    already broken into short sections rather than flowing prose).

    Raises PdfExtractError (not a bare pypdf exception) for anything
    unreadable, so rest_views.py has exactly one exception type to
    catch and turn into a 400, regardless of which underlying failure
    mode (corrupt file, password-protected, zero extractable text)
    produced it.
    """
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise PdfExtractError(f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)}MB limit")

    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except PdfReadError as exc:
        raise PdfExtractError(f"could not read PDF: {exc}") from exc

    if reader.is_encrypted:
        # pypdf can sometimes open an encrypted PDF with an empty owner
        # password (common for "export to PDF" tools that set an owner
        # password but no real user password) — try that once before
        # giving up, rather than rejecting every encrypted file outright.
        try:
            reader.decrypt("")
        except Exception:
            pass
        if reader.is_encrypted:
            raise PdfExtractError("PDF is password-protected")

    pages = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            # A single malformed page shouldn't sink the whole document —
            # skip it and keep going, same "don't let one bad piece take
            # down the whole thing" posture as knowledge/documents.py's
            # retrieve_context safe-degrade.
            page_text = ""
        page_text = page_text.strip()
        if page_text:
            pages.append(page_text)

    text = "\n\n".join(pages).strip()
    if not text:
        raise PdfExtractError(
            "no extractable text found — this PDF may be a scanned image "
            "rather than real text (OCR is not supported)"
        )
    return text
