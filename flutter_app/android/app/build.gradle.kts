import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing (2026-08-31, moving to production distribution):
// reads android/key.properties, which is gitignored and never committed
// (see .gitignore addition alongside this change) -- the actual
// keystore file and its passwords must never enter version control,
// since anyone holding them could sign a malicious app that looks like
// a legitimate update to this one on a user's phone. This file simply
// doesn't exist in this delivered zip; Ed generates it himself (see the
// setup instructions accompanying this change) and it never leaves his
// machine except into the keystore's own secure backup.
val keystorePropertiesFile = rootProject.file("key.properties")
val keystoreProperties = Properties()
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.lenkra.conversationcopilot"
    // Pinned explicitly rather than left as flutter.compileSdkVersion
    // (2026-09-01, iOS-build session — real build failure on Windows):
    // flutter_plugin_android_lifecycle (a transitive dep of file_picker)
    // now requires compileSdk 36+, but the Flutter SDK's own default
    // compileSdkVersion was still resolving to 34 on the machine that
    // hit this. Pinning here means this project's Android build target
    // no longer silently drifts with whatever Flutter SDK version
    // happens to be installed locally -- it's an explicit, version-
    // controlled decision instead. 36 satisfies the plugin's stated
    // minimum; bump this again if a future plugin update raises the
    // floor further.
    compileSdk = 36
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // Real application ID (2026-08-31) -- was the com.example.flutter_app
        // placeholder, unchanged since project creation. This is permanent
        // once real users install the app: changing it later is a new app
        // install for them, not an update, so it's fixed now, before any
        // production distribution.
        applicationId = "com.lenkra.conversationcopilot"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = keystoreProperties["storeFile"]?.let { rootProject.file(it) }
                storePassword = keystoreProperties["storePassword"] as String
            }
            // If key.properties doesn't exist (e.g. a fresh checkout before
            // Ed has generated his keystore locally), this signingConfig
            // ends up with null fields -- release builds will then fail
            // with a clear Gradle signing error rather than silently
            // falling back to the debug key, which is the mistake this
            // whole change exists to fix.
        }
    }

    buildTypes {
        release {
            signingConfig = if (keystorePropertiesFile.exists()) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}