import 'package:flutter/material.dart';

import '../auth/auth_repository.dart';
import '../main.dart' show PingTestScreen;
import '../theme.dart';
import 'knowledge_base_screen.dart';

/// Matches design/mockups/settings_mockup.html. Answer-length default and
/// the diarization toggle are in-memory only — there's no settings
/// persistence layer yet (matches the "no persistence" state noted
/// throughout this build), so they reset on app restart. STT
/// provider/API key/server address rows are the "Advanced" section kept
/// visually secondary per the mockup's stated rationale; they're
/// read-only display here (the actual server URL live in
/// live_session_screen.dart's own field, unaffected by this screen for
/// now) — wiring a shared config store is follow-up work.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, this.showBackButton = true, this.onSignOut});

  final bool showBackButton;

  /// Multi-tenancy retrofit (2026-08-30). Null when SettingsScreen is
  /// reached somewhere that isn't the bottom-nav tab root (there is no
  /// such call site today, but this keeps the constructor from forcing a
  /// callback on every future usage) — the Account section simply omits
  /// the sign-out row in that case rather than crash on a missing
  /// callback.
  final Future<void> Function()? onSignOut;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

enum _AnswerLengthDefault { quick, medium, detailed }

class _SettingsScreenState extends State<SettingsScreen> {
  _AnswerLengthDefault _answerLength = _AnswerLengthDefault.medium;
  bool _diarization = true;

  AuthUser? _account;
  bool _accountLoading = true;

  @override
  void initState() {
    super.initState();
    _loadAccount();
  }

  Future<void> _loadAccount() async {
    final authRepository = AuthRepository();
    final user = await authRepository.me();
    authRepository.dispose();
    if (!mounted) return;
    setState(() {
      _account = user;
      _accountLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: 'Settings',
        onBack: widget.showBackButton ? () => Navigator.of(context).pop() : null,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
        children: [
          const SectionLabel('Session Defaults', topPad: 4),
          CopilotGroup(
            children: [
              _SettingsRow(
                label: 'Answer length',
                trailing: _MiniToggle(
                  options: const ['quick', 'medium', 'detailed'],
                  selected: _answerLength.name,
                  onSelect: (v) => setState(
                    () => _answerLength = _AnswerLengthDefault.values
                        .firstWhere((e) => e.name == v),
                  ),
                ),
              ),
              _SettingsRow(
                label: 'Speaker diarization',
                sublabel: 'more accurate, uses more battery',
                trailing: _Switch(
                  value: _diarization,
                  onChanged: (v) => setState(() => _diarization = v),
                ),
              ),
              _SettingsRow(
                label: 'Knowledge base',
                sublabel: 'notes and docs the assistant can draw on',
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const KnowledgeBaseScreen()),
                ),
              ),
              const _SettingsRow(
                label: 'Domain mode',
                disabled: true,
                trailing: _SoonTag(),
              ),
            ],
          ),
          const SectionLabel('Advanced'),
          CopilotGroup(
            children: [
              _SettingsRow(
                label: 'STT provider',
                sublabel: 'cloud (Groq), falls back to local',
                value: 'Groq',
                accentValue: true,
                onTap: () => _showInfo(context, 'STT provider',
                    'Configured server-side via ai/cloud_router.py — not yet editable from the app.'),
              ),
              _SettingsRow(
                label: 'API key',
                value: '••••••4f2a',
                onTap: () => _showInfo(context, 'API key',
                    'Server-side credential — not yet editable from the app.'),
              ),
              _SettingsRow(
                label: 'Server address',
                value: 'sslip.io',
                onTap: () => _showInfo(context, 'Server address',
                    'Currently set per-screen on Live Session\'s connect field.'),
              ),
              _SettingsRow(
                label: 'Connectivity test',
                sublabel: 'Phase 0 WS ping harness',
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const PingTestScreen()),
                ),
              ),
            ],
          ),
          const SectionLabel('About'),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: CopilotColors.panel,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: CopilotColors.blueDim,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: const Icon(Icons.verified_outlined,
                      size: 17, color: CopilotColors.blue),
                ),
                const SizedBox(width: 12),
                const Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Development build',
                        style: TextStyle(fontSize: 13, color: CopilotColors.paper)),
                    SizedBox(height: 2),
                    Text('licensing not wired up yet',
                        style: TextStyle(
                            fontFamily: CopilotFonts.mono,
                            fontSize: 10,
                            color: CopilotColors.mist)),
                  ],
                ),
              ],
            ),
          ),
          const SectionLabel('Account'),
          _AccountSection(
            user: _account,
            loading: _accountLoading,
            onSignOut: widget.onSignOut == null
                ? null
                : () => _confirmSignOut(context, widget.onSignOut!),
          ),
          const SectionLabel('Support'),
          CopilotGroup(
            children: [
              _SettingsRow(
                label: 'Help & FAQ',
                onTap: () => _showInfo(context, 'Help & FAQ', 'Not written yet.'),
              ),
              _SettingsRow(
                label: 'Privacy policy',
                onTap: () => _showInfo(context, 'Privacy policy', 'Not written yet.'),
              ),
            ],
          ),
          const Padding(
            padding: EdgeInsets.only(top: 18),
            child: Center(
              child: Text(
                'Conversation Copilot · v0.1.0',
                style: TextStyle(
                    fontFamily: CopilotFonts.mono, fontSize: 10, color: CopilotColors.mist),
              ),
            ),
          ),
        ],
      ),
    );
  }

  void _showInfo(BuildContext context, String title, String body) {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: CopilotColors.panel,
        title: Text(title, style: const TextStyle(color: CopilotColors.paper)),
        content: Text(body, style: const TextStyle(color: CopilotColors.mist)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmSignOut(
    BuildContext context,
    Future<void> Function() onSignOut,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: CopilotColors.panel,
        title: const Text('Sign out?', style: TextStyle(color: CopilotColors.paper)),
        content: const Text(
          "You'll need your email and password to sign back in.",
          style: TextStyle(color: CopilotColors.mist),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Sign out', style: TextStyle(color: Colors.redAccent)),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await onSignOut();
    }
  }
}

/// Account status + quota snapshot, matching the design doc's "the
/// Flutter app checks quota state via /api/auth/me/" note. Sits as its
/// own CopilotGroup rather than individual _SettingsRow entries because
/// the usage figures need more room than a single-line row comfortably
/// gives — this mirrors how the admin control room shows the same
/// numbers, just read-only here.
class _AccountSection extends StatelessWidget {
  const _AccountSection({required this.user, required this.loading, this.onSignOut});

  final AuthUser? user;
  final bool loading;
  final Future<void> Function()? onSignOut;

  @override
  Widget build(BuildContext context) {
    if (loading) {
      return const CopilotGroup(children: [
        Padding(
          padding: EdgeInsets.all(14),
          child: Text('Loading account…', style: TextStyle(color: CopilotColors.mist)),
        ),
      ]);
    }

    final account = user;
    if (account == null) {
      return const CopilotGroup(children: [
        Padding(
          padding: EdgeInsets.all(14),
          child: Text(
            "Couldn't load account details.",
            style: TextStyle(color: CopilotColors.mist),
          ),
        ),
      ]);
    }

    final quota = account.quota;
    return CopilotGroup(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(account.email, style: const TextStyle(color: CopilotColors.paper, fontSize: 14)),
              if (quota != null) ...[
                const SizedBox(height: 10),
                _QuotaLine(
                  label: 'Sessions',
                  used: quota.sessionsUsed,
                  max: quota.maxSessions,
                ),
                const SizedBox(height: 4),
                _QuotaLine(
                  label: 'Minutes',
                  used: quota.minutesUsed.round(),
                  max: quota.maxMinutes,
                ),
                if (!quota.isActive) ...[
                  const SizedBox(height: 8),
                  const Text(
                    'Access has been paused by the account owner.',
                    style: TextStyle(color: Colors.redAccent, fontSize: 12),
                  ),
                ],
              ],
            ],
          ),
        ),
        if (onSignOut != null)
          _SettingsRow(
            label: 'Sign out',
            labelColor: Colors.redAccent,
            onTap: () {
              onSignOut!();
            },
          ),
      ],
    );
  }
}

class _QuotaLine extends StatelessWidget {
  const _QuotaLine({required this.label, required this.used, required this.max});

  final String label;
  final int used;
  final int? max;

  @override
  Widget build(BuildContext context) {
    final text = max == null ? '$used used · unlimited' : '$used / $max used';
    return Row(
      children: [
        SizedBox(
          width: 70,
          child: Text(label, style: const TextStyle(color: CopilotColors.mist, fontSize: 12)),
        ),
        Text(
          text,
          style: const TextStyle(
            fontFamily: CopilotFonts.mono,
            color: CopilotColors.paper,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}

class _SettingsRow extends StatelessWidget {
  const _SettingsRow({
    required this.label,
    this.sublabel,
    this.value,
    this.accentValue = false,
    this.trailing,
    this.disabled = false,
    this.onTap,
    this.labelColor,
  });

  final String label;
  final String? sublabel;
  final String? value;
  final bool accentValue;
  final Widget? trailing;
  final bool disabled;
  final VoidCallback? onTap;
  final Color? labelColor;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    label,
                    style: TextStyle(
                      fontSize: 13.5,
                      color: disabled
                          ? CopilotColors.mist
                          : (labelColor ?? CopilotColors.paper),
                    ),
                  ),
                  if (sublabel != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        sublabel!,
                        style: const TextStyle(
                            fontFamily: CopilotFonts.mono,
                            fontSize: 10,
                            color: CopilotColors.mist),
                      ),
                    ),
                ],
              ),
            ),
            if (trailing != null)
              trailing!
            else if (value != null) ...[
              Text(
                value!,
                style: TextStyle(
                  fontFamily: CopilotFonts.mono,
                  fontSize: 12,
                  color: accentValue ? CopilotColors.amber : CopilotColors.mist,
                ),
              ),
              const SizedBox(width: 8),
              const Icon(Icons.chevron_right, size: 13, color: CopilotColors.mist),
            ] else if (onTap != null)
              const Icon(Icons.chevron_right, size: 13, color: CopilotColors.mist),
          ],
        ),
      ),
    );
  }
}

class _MiniToggle extends StatelessWidget {
  const _MiniToggle({required this.options, required this.selected, required this.onSelect});

  final List<String> options;
  final String selected;
  final ValueChanged<String> onSelect;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: CopilotColors.panelRaised,
        borderRadius: BorderRadius.circular(7),
      ),
      padding: const EdgeInsets.all(2),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final o in options)
            GestureDetector(
              onTap: () => onSelect(o),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: o == selected ? CopilotColors.ink : null,
                  borderRadius: BorderRadius.circular(5),
                ),
                child: Text(
                  o,
                  style: TextStyle(
                    fontFamily: CopilotFonts.mono,
                    fontSize: 9.5,
                    color: o == selected ? CopilotColors.amber : CopilotColors.mist,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _Switch extends StatelessWidget {
  const _Switch({required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => onChanged(!value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        width: 38,
        height: 22,
        padding: const EdgeInsets.all(2),
        decoration: BoxDecoration(
          color: value ? CopilotColors.amberDim : CopilotColors.panelRaised,
          borderRadius: BorderRadius.circular(11),
        ),
        child: AnimatedAlign(
          duration: const Duration(milliseconds: 150),
          alignment: value ? Alignment.centerRight : Alignment.centerLeft,
          child: Container(
            width: 18,
            height: 18,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: value ? CopilotColors.amber : CopilotColors.mist,
            ),
          ),
        ),
      ),
    );
  }
}

class _SoonTag extends StatelessWidget {
  const _SoonTag();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: CopilotColors.panelRaised,
        borderRadius: BorderRadius.circular(5),
      ),
      child: const Text(
        'SOON',
        style: TextStyle(
          fontFamily: CopilotFonts.mono,
          fontSize: 9,
          letterSpacing: 0.5,
          color: CopilotColors.mist,
        ),
      ),
    );
  }
}
