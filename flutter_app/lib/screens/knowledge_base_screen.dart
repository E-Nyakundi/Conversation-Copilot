import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../data/knowledge_repository.dart';
import '../models/knowledge_document.dart';
import '../theme.dart';

/// Phase 6 (2026-08-24) — the missing Flutter half of the personal
/// knowledge base. The backend (knowledge/ Django app: chunking,
/// sentence-transformers embeddings, FAISS retrieval) has been wired
/// end-to-end into every suggestion since the prior session; this screen
/// is the first way to actually get a document into it from the app,
/// instead of the Django admin or a raw REST call.
///
/// Deliberately modest initial scope: paste-or-type text upload here (no
/// file picker on this screen, initially). PDF upload (CV/LinkedIn/GitHub
/// profile export — see knowledge/pdf_extract.py server-side) was added
/// 2026-08-31 (build plan §15) but only through the onboarding flow's
/// dedicated step (onboarding_screen.dart) at first.
///
/// 2026-09-01: extended to this screen too — a user who skipped the
/// onboarding upload step, or who's on an existing account from before
/// PDF upload existed at all, had no way to add a PDF later short of the
/// paste-text workaround. _UploadSheet below now offers both text entry
/// and a PDF picker (reusing KnowledgeRepository.uploadPdf, same as
/// onboarding_screen.dart), so this is the one place either upload path
/// is available regardless of how the account got its knowledge base
/// started.
/// — mirrors SessionHistoryScreen's structure (search/list/detail-push
/// pattern) since that's the closest existing precedent for a "list of
/// things fetched from a REST endpoint" screen in this app.
///
/// Reachable from Settings (see settings_screen.dart's new "Knowledge
/// base" row) rather than a fourth bottom-nav tab — matches the existing
/// "Advanced"/secondary-feature treatment already used for
/// STT-provider/API-key/server-address rows on that screen, and avoids
/// reworking AppShell's three-tab NavigationBar for a feature that isn't
/// part of the core record-a-session loop.
class KnowledgeBaseScreen extends StatefulWidget {
  const KnowledgeBaseScreen({super.key});

  @override
  State<KnowledgeBaseScreen> createState() => _KnowledgeBaseScreenState();
}

class _KnowledgeBaseScreenState extends State<KnowledgeBaseScreen> {
  final _repository = KnowledgeRepository();
  late Future<List<KnowledgeDocument>> _documentsFuture;

  @override
  void initState() {
    super.initState();
    _documentsFuture = _repository.fetchDocuments();
  }

  @override
  void dispose() {
    _repository.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _documentsFuture = _repository.fetchDocuments();
    });
    await _documentsFuture;
  }

  Future<void> _openUploadSheet() async {
    final uploaded = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _UploadSheet(repository: _repository),
    );
    if (uploaded == true) {
      await _refresh();
    }
  }

  Future<void> _openDocument(KnowledgeDocument summary) async {
    final deleted = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => _KnowledgeDocumentDetailScreen(
          documentId: summary.id,
          repository: _repository,
        ),
      ),
    );
    if (deleted == true) {
      await _refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: 'Knowledge Base',
        onBack: () => Navigator.of(context).pop(),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _openUploadSheet,
        backgroundColor: CopilotColors.amber,
        foregroundColor: CopilotColors.ink,
        child: const Icon(Icons.add),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        color: CopilotColors.amber,
        backgroundColor: CopilotColors.panel,
        child: FutureBuilder<List<KnowledgeDocument>>(
          future: _documentsFuture,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  SizedBox(height: 60),
                  Center(
                    child: SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: CopilotColors.amber,
                      ),
                    ),
                  ),
                ],
              );
            }
            if (snapshot.hasError) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: [
                  const SizedBox(height: 60),
                  const Icon(Icons.cloud_off, color: CopilotColors.mist, size: 22),
                  const SizedBox(height: 10),
                  const Center(
                    child: Text(
                      'Couldn\'t load your knowledge base.',
                      style: TextStyle(color: CopilotColors.mist, fontSize: 12.5),
                    ),
                  ),
                  const SizedBox(height: 10),
                  Center(
                    child: GestureDetector(
                      onTap: _refresh,
                      child: const Text(
                        'Retry',
                        style: TextStyle(
                          fontFamily: CopilotFonts.mono,
                          fontSize: 11,
                          color: CopilotColors.blue,
                        ),
                      ),
                    ),
                  ),
                ],
              );
            }

            final documents = snapshot.data ?? const [];
            if (documents.isEmpty) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  SizedBox(height: 60),
                  _EmptyKnowledgeBase(),
                ],
              );
            }

            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 90),
              children: [
                const Text(
                  'Notes and documents here are available to every future '
                  'session — the assistant pulls from them automatically '
                  'when relevant, no per-session setup needed.',
                  style: TextStyle(color: CopilotColors.mist, fontSize: 11.5, height: 1.4),
                ),
                const SizedBox(height: 16),
                for (final doc in documents)
                  _DocumentCard(document: doc, onTap: () => _openDocument(doc)),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _DocumentCard extends StatelessWidget {
  const _DocumentCard({required this.document, required this.onTap});

  final KnowledgeDocument document;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 9),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            const Icon(Icons.description_outlined, size: 16, color: CopilotColors.blue),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    document.title,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 13.5, fontWeight: FontWeight.w500, color: CopilotColors.paper),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    '${document.chunkCount} chunk${document.chunkCount == 1 ? '' : 's'} · '
                    '${document.textLength} chars',
                    style: const TextStyle(
                        fontFamily: CopilotFonts.mono, fontSize: 10.5, color: CopilotColors.mist),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, size: 14, color: CopilotColors.mist),
          ],
        ),
      ),
    );
  }
}

class _EmptyKnowledgeBase extends StatelessWidget {
  const _EmptyKnowledgeBase();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 40),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.description_outlined, color: CopilotColors.mist, size: 28),
            const SizedBox(height: 10),
            const Text(
              'No documents yet',
              style: TextStyle(color: CopilotColors.paper, fontSize: 13.5, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 6),
            const Text(
              'Add notes, background info, or reference material — the '
              'assistant will pull from it automatically during sessions.',
              textAlign: TextAlign.center,
              style: TextStyle(color: CopilotColors.mist, fontSize: 11.5, height: 1.4),
            ),
          ],
        ),
      ),
    );
  }
}

/// Bottom sheet for pasting/typing a new document. Synchronous
/// upload+index per the backend's ingest() — the button shows a spinner
/// for the whole round trip since there's no separate "processing"
/// state to poll for.
class _UploadSheet extends StatefulWidget {
  const _UploadSheet({required this.repository});

  final KnowledgeRepository repository;

  @override
  State<_UploadSheet> createState() => _UploadSheetState();
}

class _UploadSheetState extends State<_UploadSheet> {
  final _titleController = TextEditingController();
  final _textController = TextEditingController();
  bool _submitting = false;
  String? _error;

  // PDF upload (2026-09-01): same file_picker 12.x pattern as
  // onboarding_screen.dart's _pickFile/_uploadPickedFile -- pickFile()
  // returns PlatformFile? directly, and bytes are read on demand via
  // readAsBytes() rather than a pick-time withData flag (removed in
  // file_picker 12.x's federated rewrite).
  PlatformFile? _pickedFile;

  Future<void> _pickPdf() async {
    final file = await FilePicker.pickFile(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
    );
    if (file == null) return;
    setState(() {
      _pickedFile = file;
      _error = null;
    });
  }

  void _clearPickedFile() {
    setState(() => _pickedFile = null);
  }

  @override
  void dispose() {
    _titleController.dispose();
    _textController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final pickedFile = _pickedFile;
    if (pickedFile == null && _textController.text.trim().isEmpty) {
      setState(() => _error = 'Paste or type some text, or choose a PDF, first.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      if (pickedFile != null) {
        final bytes = await pickedFile.readAsBytes();
        await widget.repository.uploadPdf(
          bytes: bytes,
          filename: pickedFile.name,
          title: _titleController.text,
        );
      } else {
        await widget.repository.uploadDocument(
          title: _titleController.text,
          text: _textController.text,
        );
      }
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = 'Upload failed: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final viewInsets = MediaQuery.of(context).viewInsets;
    return Padding(
      padding: EdgeInsets.only(bottom: viewInsets.bottom),
      child: Container(
        decoration: const BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: CopilotColors.hairline,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const Text(
              'Add to knowledge base',
              style: TextStyle(
                  fontFamily: CopilotFonts.display,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                  color: CopilotColors.paper),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _titleController,
              enabled: !_submitting,
              style: const TextStyle(color: CopilotColors.paper, fontSize: 13.5),
              decoration: const InputDecoration(
                labelText: 'Title (optional)',
                labelStyle: TextStyle(color: CopilotColors.mist),
                filled: true,
                fillColor: CopilotColors.panelRaised,
                border: OutlineInputBorder(borderSide: BorderSide.none),
              ),
            ),
            const SizedBox(height: 10),
            // PDF upload (2026-09-01): same file_picker choice as the
            // onboarding flow's step -- offered as an alternative to the
            // text field below, not alongside it (the "PASTE OR TYPE
            // TEXT INSTEAD" divider below marks that split).
            if (_pickedFile == null)
              OutlinedButton.icon(
                onPressed: _submitting ? null : _pickPdf,
                icon: const Icon(Icons.upload_file, size: 16),
                label: const Text('Choose a PDF'),
              )
            else
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                decoration: BoxDecoration(
                  color: CopilotColors.panelRaised,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.picture_as_pdf, color: CopilotColors.amber, size: 18),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        _pickedFile!.name,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12.5, color: CopilotColors.paper),
                      ),
                    ),
                    IconButton(
                      onPressed: _submitting ? null : _clearPickedFile,
                      icon: const Icon(Icons.close, size: 16, color: CopilotColors.mist),
                      tooltip: 'Remove',
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 14),
            if (_pickedFile == null) ...[
              const Text(
                'OR PASTE / TYPE TEXT INSTEAD',
                style: TextStyle(
                  fontFamily: CopilotFonts.mono,
                  fontSize: 9.5,
                  letterSpacing: 1,
                  color: CopilotColors.mist,
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: _textController,
                enabled: !_submitting,
                maxLines: 8,
                minLines: 5,
                style: const TextStyle(color: CopilotColors.paper, fontSize: 13),
                decoration: const InputDecoration(
                  labelText: 'Text',
                  hintText: 'Paste notes, background info, reference material...',
                  labelStyle: TextStyle(color: CopilotColors.mist),
                  hintStyle: TextStyle(color: CopilotColors.mist),
                  filled: true,
                  fillColor: CopilotColors.panelRaised,
                  border: OutlineInputBorder(borderSide: BorderSide.none),
                ),
              ),
            ],
            if (_error != null) ...[
              const SizedBox(height: 10),
              Text(_error!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
            ],
            const SizedBox(height: 14),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _submitting ? null : _submit,
                style: ElevatedButton.styleFrom(
                  backgroundColor: CopilotColors.amber,
                  foregroundColor: CopilotColors.ink,
                  padding: const EdgeInsets.symmetric(vertical: 13),
                ),
                child: _submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2, color: CopilotColors.ink),
                      )
                    : const Text('Upload'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Full text + chunk view for one document, with delete. Pushed from the
/// list card, matching SessionDetailScreen's pattern of a separate
/// pushed route rather than expand-in-place.
class _KnowledgeDocumentDetailScreen extends StatefulWidget {
  const _KnowledgeDocumentDetailScreen({
    required this.documentId,
    required this.repository,
  });

  final String documentId;
  final KnowledgeRepository repository;

  @override
  State<_KnowledgeDocumentDetailScreen> createState() =>
      _KnowledgeDocumentDetailScreenState();
}

class _KnowledgeDocumentDetailScreenState
    extends State<_KnowledgeDocumentDetailScreen> {
  late Future<KnowledgeDocument> _documentFuture;
  bool _deleting = false;

  @override
  void initState() {
    super.initState();
    _documentFuture = widget.repository.fetchDocument(widget.documentId);
  }

  Future<void> _confirmDelete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: CopilotColors.panel,
        title: const Text('Delete document?', style: TextStyle(color: CopilotColors.paper)),
        content: const Text(
          'This removes it from the knowledge base — future suggestions will no longer draw on it.',
          style: TextStyle(color: CopilotColors.mist),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Delete', style: TextStyle(color: Colors.redAccent)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _deleting = true);
    try {
      await widget.repository.deleteDocument(widget.documentId);
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _deleting = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Delete failed: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: 'Document',
        onBack: () => Navigator.of(context).pop(),
      ),
      body: FutureBuilder<KnowledgeDocument>(
        future: _documentFuture,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(
              child: SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2, color: CopilotColors.amber),
              ),
            );
          }
          if (snapshot.hasError || !snapshot.hasData) {
            return const Center(
              child: Text(
                'Couldn\'t load this document.',
                style: TextStyle(color: CopilotColors.mist, fontSize: 12.5),
              ),
            );
          }

          final document = snapshot.data!;
          return ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 30),
            children: [
              Text(
                document.title,
                style: const TextStyle(
                    fontFamily: CopilotFonts.display,
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                    color: CopilotColors.paper),
              ),
              const SizedBox(height: 6),
              Text(
                '${document.chunkCount} chunk${document.chunkCount == 1 ? '' : 's'} · '
                '${document.textLength} chars',
                style: const TextStyle(
                    fontFamily: CopilotFonts.mono, fontSize: 10.5, color: CopilotColors.mist),
              ),
              const SizedBox(height: 16),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: CopilotColors.panel,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  document.text ?? '',
                  style: const TextStyle(color: CopilotColors.paper, fontSize: 13, height: 1.5),
                ),
              ),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: _deleting ? null : _confirmDelete,
                  icon: _deleting
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.redAccent),
                        )
                      : const Icon(Icons.delete_outline, size: 16, color: Colors.redAccent),
                  label: const Text('Delete document', style: TextStyle(color: Colors.redAccent)),
                  style: OutlinedButton.styleFrom(
                    side: const BorderSide(color: Colors.redAccent),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}
