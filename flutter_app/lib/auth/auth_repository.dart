import 'dart:convert';

import 'package:http/http.dart' as http;

import '../api_config.dart';
import 'auth_store.dart';

/// Quota snapshot as returned by /api/auth/login/, /me/, and
/// /change-password/ (accounts/views.py's _quota_summary — same shape
/// from all three, so one model covers all of them).
class QuotaStatus {
  const QuotaStatus({
    required this.maxSessions,
    required this.sessionsUsed,
    required this.sessionsRemaining,
    required this.maxMinutes,
    required this.minutesUsed,
    required this.minutesRemaining,
    required this.isActive,
  });

  final int? maxSessions;
  final int sessionsUsed;
  final int? sessionsRemaining;
  final int? maxMinutes;
  final double minutesUsed;
  final int? minutesRemaining;
  final bool isActive;

  factory QuotaStatus.fromJson(Map<String, dynamic> json) {
    return QuotaStatus(
      maxSessions: (json['max_sessions'] as num?)?.toInt(),
      sessionsUsed: (json['sessions_used'] as num?)?.toInt() ?? 0,
      sessionsRemaining: (json['sessions_remaining'] as num?)?.toInt(),
      maxMinutes: (json['max_minutes'] as num?)?.toInt(),
      minutesUsed: (json['minutes_used'] as num?)?.toDouble() ?? 0,
      minutesRemaining: (json['minutes_remaining'] as num?)?.toInt(),
      isActive: json['is_active'] as bool? ?? true,
    );
  }
}

class AuthUser {
  const AuthUser({
    required this.id,
    required this.email,
    required this.isStaff,
    required this.mustChangePassword,
    this.quota,
  });

  final int id;
  final String email;
  final bool isStaff;
  final bool mustChangePassword;
  final QuotaStatus? quota;

  factory AuthUser.fromJson(Map<String, dynamic> json) {
    final quotaJson = json['quota'] as Map<String, dynamic>?;
    return AuthUser(
      id: (json['id'] as num).toInt(),
      email: json['email'] as String? ?? '',
      isStaff: json['is_staff'] as bool? ?? false,
      mustChangePassword: json['must_change_password'] as bool? ?? false,
      quota: quotaJson != null ? QuotaStatus.fromJson(quotaJson) : null,
    );
  }
}

/// Talks to accounts/views.py's tenant-facing auth endpoints
/// (/api/auth/login/, /change-password/, /me/). Mirrors
/// SessionRepository's shape (own http.Client, a matching *Exception
/// type) rather than introducing a different pattern for this one
/// repository.
class AuthRepository {
  AuthRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Future<AuthUser> login(String email, String password) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/auth/login/');
    final response = await _client.post(
      uri,
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    );
    final body = _decodeOrThrow(response);
    await AuthStore.instance.saveToken(body['token'] as String);
    return AuthUser.fromJson(body['user'] as Map<String, dynamic>);
  }

  Future<AuthUser> changePassword(String newPassword) async {
    final token = await AuthStore.instance.readToken();
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/auth/change-password/');
    final response = await _client.post(
      uri,
      headers: {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      },
      body: jsonEncode({'new_password': newPassword}),
    );
    final body = _decodeOrThrow(response);
    // Server rotates the token on password change (accounts/services.py)
    // — the old one this request authenticated with is dead the moment
    // this response lands, so the new one must replace it immediately,
    // not just be returned and ignored.
    await AuthStore.instance.saveToken(body['token'] as String);
    return AuthUser.fromJson(body['user'] as Map<String, dynamic>);
  }

  /// GET /api/auth/me/ — used at cold start to check whether a saved
  /// token is still valid (and to refresh the quota snapshot) before
  /// deciding whether to show the login screen or the app shell.
  /// Returns null on any auth failure (401, network error) rather than
  /// throwing — AppRoot treats "couldn't confirm" the same as "not
  /// logged in", which is the safe default either way.
  Future<AuthUser?> me() async {
    final token = await AuthStore.instance.readToken();
    if (token == null) return null;
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/auth/me/');
    try {
      final response = await _client.get(
        uri,
        headers: {'Authorization': 'Bearer $token'},
      );
      if (response.statusCode != 200) return null;
      return AuthUser.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    } catch (_) {
      // Network error at cold start: don't force a logout over a
      // transient connectivity blip — but also don't claim success.
      // Callers that need "definitely logged in" should treat null the
      // same as a real auth failure; this deliberately doesn't
      // distinguish the two cases since the app has no offline mode to
      // fall back to anyway.
      return null;
    }
  }

  Map<String, dynamic> _decodeOrThrow(http.Response response) {
    Map<String, dynamic> body = const {};
    try {
      body = jsonDecode(response.body) as Map<String, dynamic>;
    } catch (_) {
      // fall through to status-code-based error below
    }
    if (response.statusCode != 200 && response.statusCode != 201) {
      throw AuthException(
        (body['error'] as String?) ?? 'Request failed (${response.statusCode})',
      );
    }
    return body;
  }

  void dispose() {
    _client.close();
  }
}

class AuthException implements Exception {
  AuthException(this.message);
  final String message;

  @override
  String toString() => message;
}
