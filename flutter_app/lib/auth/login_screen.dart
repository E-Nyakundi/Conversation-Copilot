import 'package:flutter/material.dart';

import '../theme.dart';
import 'auth_repository.dart';

/// Multi-tenancy retrofit (2026-08-30). Styled to match the rest of the
/// app's "instrument panel" theme (theme.dart) rather than introducing a
/// separate visual language just for auth screens.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onSignedIn});

  /// Called with the freshly-authenticated user once login succeeds.
  /// AppRoot decides where to route from there (change-password vs.
  /// straight into the app shell) — this screen doesn't know or care.
  final void Function(AuthUser user) onSignedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _authRepository = AuthRepository();

  bool _busy = false;
  String? _error;
  // Password visibility toggle -- defaults hidden (obscureText: true),
  // same posture as before this change; the user opts into revealing it.
  bool _passwordVisible = false;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _authRepository.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final user = await _authRepository.login(
        _emailController.text.trim(),
        _passwordController.text,
      );
      if (!mounted) return;
      widget.onSignedIn(user);
    } on AuthException catch (e) {
      setState(() => _error = e.message);
    } catch (e) {
      setState(() => _error = 'Could not reach the server.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 28),
            child: Form(
              key: _formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'CONVERSATION COPILOT',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: CopilotFonts.mono,
                      fontSize: 11,
                      letterSpacing: 2,
                      color: CopilotColors.mist,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Sign in',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: CopilotFonts.display,
                      fontSize: 26,
                      fontWeight: FontWeight.w600,
                      color: CopilotColors.paper,
                    ),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    'Use the email and temporary password you were given.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: CopilotColors.mist, fontSize: 13),
                  ),
                  const SizedBox(height: 28),
                  CopilotGroup(
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                        child: TextFormField(
                          controller: _emailController,
                          keyboardType: TextInputType.emailAddress,
                          autofillHints: const [AutofillHints.email],
                          style: const TextStyle(color: CopilotColors.paper),
                          decoration: const InputDecoration(
                            labelText: 'Email',
                            border: InputBorder.none,
                            labelStyle: TextStyle(color: CopilotColors.mist),
                          ),
                          validator: (value) =>
                              (value == null || value.trim().isEmpty) ? 'Required' : null,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                        child: TextFormField(
                          controller: _passwordController,
                          obscureText: !_passwordVisible,
                          autofillHints: const [AutofillHints.password],
                          style: const TextStyle(color: CopilotColors.paper),
                          decoration: InputDecoration(
                            labelText: 'Password',
                            border: InputBorder.none,
                            labelStyle: const TextStyle(color: CopilotColors.mist),
                            suffixIcon: IconButton(
                              icon: Icon(
                                _passwordVisible ? Icons.visibility_off : Icons.visibility,
                                color: CopilotColors.mist,
                                size: 20,
                              ),
                              tooltip: _passwordVisible ? 'Hide password' : 'Show password',
                              onPressed: () => setState(() => _passwordVisible = !_passwordVisible),
                            ),
                          ),
                          validator: (value) =>
                              (value == null || value.isEmpty) ? 'Required' : null,
                          onFieldSubmitted: (_) => _submit(),
                        ),
                      ),
                    ],
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 14),
                    Text(
                      _error!,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.redAccent, fontSize: 13),
                    ),
                  ],
                  const SizedBox(height: 22),
                  ElevatedButton(
                    onPressed: _busy ? null : _submit,
                    child: _busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: CopilotColors.ink,
                            ),
                          )
                        : const Text('Sign in'),
                  ),
                  const SizedBox(height: 22),
                  const _InviteOnlyHint(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Invite-only contact hint (2026-09-01): the very first screen a
/// signed-out user sees had zero indication that this app doesn't have
/// self-serve signup -- someone with no account and no context would
/// just see a login form with nothing to do. Shown unconditionally
/// below the sign-in form for every signed-out visit (not tied to the
/// tour/onboarding system in tour/, which only starts running once
/// there's an authenticated AppShell to walk through -- this has to work
/// before any of that exists). Plain SelectableText rather than a
/// tappable wa.me/mailto: link since url_launcher isn't already a
/// dependency here and copy-paste is a fine fallback for a small,
/// low-frequency screen.
class _InviteOnlyHint extends StatelessWidget {
  const _InviteOnlyHint();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CopilotColors.panel,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: CopilotColors.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.info_outline, size: 15, color: CopilotColors.blue),
              const SizedBox(width: 8),
              const Text(
                "Don't have an account?",
                style: TextStyle(
                  fontFamily: CopilotFonts.display,
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: CopilotColors.paper,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          const Text(
            "This app is invite-only. Reach out and I'll set one up for you:",
            style: TextStyle(color: CopilotColors.mist, fontSize: 12, height: 1.4),
          ),
          const SizedBox(height: 8),
          const SelectableText(
            'WhatsApp: +254 797 554054',
            style: TextStyle(
              fontFamily: CopilotFonts.mono,
              fontSize: 12,
              color: CopilotColors.blue,
            ),
          ),
          const SizedBox(height: 3),
          const SelectableText(
            'Email: e.nyakundilewis@gmail.com',
            style: TextStyle(
              fontFamily: CopilotFonts.mono,
              fontSize: 12,
              color: CopilotColors.blue,
            ),
          ),
        ],
      ),
    );
  }
}
