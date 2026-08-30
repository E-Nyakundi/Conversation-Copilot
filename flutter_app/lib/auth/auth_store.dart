import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Multi-tenancy retrofit (2026-08-30): where the app's auth token lives.
///
/// flutter_secure_storage (Keychain on iOS, EncryptedSharedPreferences on
/// Android), not shared_preferences — this token is a real bearer
/// credential (see accounts/models.py's AuthToken on the server: one live
/// token per account, works for both REST and the WebSocket handshake),
/// not a UI preference like the onboarding flag main.dart already stores
/// in plain shared_preferences. Those two storage choices are deliberately
/// different for that reason, not an inconsistency.
class AuthStore {
  AuthStore._();
  static final AuthStore instance = AuthStore._();

  static const _storage = FlutterSecureStorage();
  static const _tokenKey = 'copilot_auth_token';

  String? _cachedToken;

  Future<String?> readToken() async {
    if (_cachedToken != null) return _cachedToken;
    try {
      _cachedToken = await _storage.read(key: _tokenKey);
    } catch (_) {
      // Platform keystore unavailable (rare) — treat as logged out rather
      // than crash startup; the user will just see the login screen.
      _cachedToken = null;
    }
    return _cachedToken;
  }

  Future<void> saveToken(String token) async {
    _cachedToken = token;
    try {
      await _storage.write(key: _tokenKey, value: token);
    } catch (_) {
      // Best-effort: token still works for the rest of this process's
      // lifetime via the in-memory cache even if persistence failed; the
      // user will just be asked to log in again next cold start.
    }
  }

  Future<void> clear() async {
    _cachedToken = null;
    try {
      await _storage.delete(key: _tokenKey);
    } catch (_) {
      // Nothing more we can do; in-memory cache is already cleared, which
      // is what actually gates the rest of this session.
    }
  }
}
