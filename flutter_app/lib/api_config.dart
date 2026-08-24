/// Shared server host config. The WS screens (live_session_screen.dart,
/// main.dart's ping screen) still keep their own editable text-field
/// defaults for now — this constant is specifically for the new REST API
/// client (session_repository.dart) added for real session persistence
/// (2026-08-24). Deliberately not unifying the WS defaults into this same
/// constant in this change — that's a separate, slightly riskier touch on
/// screens that already work, and out of scope for wiring up persistence.
class ApiConfig {
  ApiConfig._();

  /// Same VM/host the WS screens point at by default
  /// (145-241-102-99.sslip.io), over HTTPS since Caddy terminates TLS
  /// there already (see Phase 0.5 in the build plan).
  static const String baseUrl = 'https://145-241-102-99.sslip.io';
}
