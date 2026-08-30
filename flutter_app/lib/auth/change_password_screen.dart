import 'package:flutter/material.dart';

import '../theme.dart';
import 'auth_repository.dart';

/// Shown when AuthUser.mustChangePassword is true — the invite-flow temp
/// password (accounts/services.py's invite_user) must be replaced before
/// the app shell is reachable. No "skip" option: this isn't optional
/// onboarding polish, it's the server-side gate that a leaked/shared
/// temp password stops working the moment the real one is set.
class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key, required this.onDone});

  final void Function(AuthUser user) onDone;

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _passwordController = TextEditingController();
  final _confirmController = TextEditingController();
  final _authRepository = AuthRepository();

  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _passwordController.dispose();
    _confirmController.dispose();
    _authRepository.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    if (_passwordController.text != _confirmController.text) {
      setState(() => _error = "Passwords don't match.");
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final user = await _authRepository.changePassword(_passwordController.text);
      if (!mounted) return;
      widget.onDone(user);
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
                    'FIRST SIGN-IN',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: CopilotFonts.mono,
                      fontSize: 11,
                      letterSpacing: 2,
                      color: CopilotColors.amber,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Set a new password',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: CopilotFonts.display,
                      fontSize: 24,
                      fontWeight: FontWeight.w600,
                      color: CopilotColors.paper,
                    ),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    "You're using a temporary password. Choose one only you know.",
                    textAlign: TextAlign.center,
                    style: TextStyle(color: CopilotColors.mist, fontSize: 13),
                  ),
                  const SizedBox(height: 28),
                  CopilotGroup(
                    children: [
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                        child: TextFormField(
                          controller: _passwordController,
                          obscureText: true,
                          style: const TextStyle(color: CopilotColors.paper),
                          decoration: const InputDecoration(
                            labelText: 'New password',
                            border: InputBorder.none,
                            labelStyle: TextStyle(color: CopilotColors.mist),
                          ),
                          validator: (value) => (value == null || value.length < 8)
                              ? 'At least 8 characters'
                              : null,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                        child: TextFormField(
                          controller: _confirmController,
                          obscureText: true,
                          style: const TextStyle(color: CopilotColors.paper),
                          decoration: const InputDecoration(
                            labelText: 'Confirm password',
                            border: InputBorder.none,
                            labelStyle: TextStyle(color: CopilotColors.mist),
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
                        : const Text('Save and continue'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
