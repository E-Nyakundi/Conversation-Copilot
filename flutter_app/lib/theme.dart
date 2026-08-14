import 'package:flutter/material.dart';

/// Design tokens for the "instrument panel" visual identity, matching
/// design/mockups/*.html exactly. See conversation_copilot_build_plan.md
/// Phase 3.5 for the rationale: the app is glanced at for a second or two
/// during a live, high-stakes conversation, so legibility at a glance beats
/// decoration. Amber = "the system is listening", blue = "here's a
/// suggestion" — both accents are load-bearing, not decorative.
class CopilotColors {
  CopilotColors._();

  static const ink = Color(0xFF10121B);
  static const panel = Color(0xFF1B1E2E);
  static const panelRaised = Color(0xFF262B40);
  static const hairline = Color(0xFF333850);
  static const amber = Color(0xFFE7A83D);
  static const amberDim = Color(0xFF4A3A1E);
  static const blue = Color(0xFF6FA3D8);
  static const blueDim = Color(0xFF23344A);
  static const paper = Color(0xFFEDE9E2);
  static const mist = Color(0xFF8A8FA6);
}

/// Font family names. The mockups pull Space Grotesk / IBM Plex Sans / IBM
/// Plex Mono from Google Fonts via <link>; the Flutter app ships without a
/// font-loading package dependency (google_fonts isn't in pubspec.yaml), so
/// these fall back to the platform default when unavailable. Swap in the
/// google_fonts package later if pixel-exact type matching matters more
/// than keeping dependencies minimal.
class CopilotFonts {
  CopilotFonts._();

  static const display = 'Space Grotesk'; // screen titles, timers
  static const mono = 'IBM Plex Mono'; // timestamps, confidence %, latency
  // Body text uses the theme's default (IBM Plex Sans equivalent budget).
}

ThemeData buildCopilotTheme() {
  final base = ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    scaffoldBackgroundColor: CopilotColors.ink,
    colorScheme: const ColorScheme.dark(
      surface: CopilotColors.ink,
      primary: CopilotColors.amber,
      secondary: CopilotColors.blue,
      onPrimary: CopilotColors.ink,
      onSurface: CopilotColors.paper,
    ),
  );
  return base.copyWith(
    textTheme: base.textTheme.apply(
      bodyColor: CopilotColors.paper,
      displayColor: CopilotColors.paper,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: CopilotColors.ink,
      foregroundColor: CopilotColors.paper,
      elevation: 0,
      titleTextStyle: TextStyle(
        fontFamily: CopilotFonts.display,
        fontWeight: FontWeight.w600,
        fontSize: 17,
        color: CopilotColors.paper,
      ),
    ),
    cardTheme: const CardThemeData(
      color: CopilotColors.panel,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(12)),
      ),
    ),
    dividerTheme: const DividerThemeData(
      color: CopilotColors.hairline,
      thickness: 1,
      space: 1,
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: CopilotColors.panel,
      indicatorColor: CopilotColors.amberDim,
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return TextStyle(
          fontFamily: CopilotFonts.mono,
          fontSize: 10.5,
          letterSpacing: 0.3,
          color: selected ? CopilotColors.amber : CopilotColors.mist,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return IconThemeData(
          color: selected ? CopilotColors.amber : CopilotColors.mist,
          size: 22,
        );
      }),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: CopilotColors.amber,
        foregroundColor: CopilotColors.ink,
        textStyle: const TextStyle(
          fontFamily: CopilotFonts.display,
          fontWeight: FontWeight.w600,
          fontSize: 14.5,
        ),
        padding: const EdgeInsets.symmetric(vertical: 15),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    ),
  );
}

/// Section label used at the top of settings-style grouped lists — mono,
/// uppercase, muted. Matches .section-label in settings_mockup.html.
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.topPad = 20});

  final String text;
  final double topPad;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(top: topPad, bottom: 8),
      child: Text(
        text.toUpperCase(),
        style: const TextStyle(
          fontFamily: CopilotFonts.mono,
          fontSize: 10.5,
          letterSpacing: 1.5,
          color: CopilotColors.mist,
        ),
      ),
    );
  }
}

/// A rounded panel-colored group container for stacked rows, matching
/// .group in the mockups.
class CopilotGroup extends StatelessWidget {
  const CopilotGroup({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: ColoredBox(
        color: CopilotColors.panel,
        child: Column(
          children: [
            for (var i = 0; i < children.length; i++) ...[
              if (i > 0) const Divider(height: 1, indent: 14, endIndent: 14),
              children[i],
            ],
          ],
        ),
      ),
    );
  }
}

/// Small back-circle + title header used by pushed detail screens
/// (History, Session Detail, Settings when pushed), matching the
/// `.back-btn` + `.header-title` pattern shared across those mockups.
/// When [onBack] is null the leading circle is simply omitted — used when
/// a screen is hosted as a bottom-nav tab root and has nothing to go back
/// to.
class CopilotHeader extends StatelessWidget implements PreferredSizeWidget {
  const CopilotHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.onBack,
    this.trailing,
  });

  final String title;
  final String? subtitle;
  final VoidCallback? onBack;
  final Widget? trailing;

  @override
  Size get preferredSize => const Size.fromHeight(52);

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      bottom: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 8),
        child: Row(
          children: [
            if (onBack != null) ...[
              GestureDetector(
                onTap: onBack,
                child: Container(
                  width: 32,
                  height: 32,
                  decoration: const BoxDecoration(
                    color: CopilotColors.panel,
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.arrow_back_ios_new,
                      size: 15, color: CopilotColors.paper),
                ),
              ),
              const SizedBox(width: 14),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    title,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontFamily: CopilotFonts.display,
                      fontWeight: FontWeight.w600,
                      fontSize: 17,
                      color: CopilotColors.paper,
                    ),
                  ),
                  if (subtitle != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        subtitle!,
                        style: const TextStyle(
                          fontFamily: CopilotFonts.mono,
                          fontSize: 10.5,
                          color: CopilotColors.mist,
                        ),
                      ),
                    ),
                ],
              ),
            ),
            if (trailing != null) trailing!,
          ],
        ),
      ),
    );
  }
}
