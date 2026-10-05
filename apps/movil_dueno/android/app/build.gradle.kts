import java.io.FileInputStream
import java.util.Properties

// Firma de release: si existe android/key.properties con storeFile (lo arma
// el CI cuando el repo tiene los secrets) se firma con esa clave fija, así
// cada APK nuevo se instala ENCIMA del anterior sin perder la sesión. Si no,
// firma de debug.
//
// Contraseñas y alias: primero de las variables de entorno
// ANDROID_KEYSTORE_PASSWORD / ANDROID_KEY_ALIAS / ANDROID_KEY_PASSWORD (el CI
// las pasa desde los secrets) y, si no están, de key.properties (para firmar
// a mano). El CI ya no las escribe en key.properties: ese formato toma la '\'
// como escape y una contraseña con '\' rompía la firma.
val propiedadesFirma = Properties().apply {
    val archivo = rootProject.file("key.properties")
    if (archivo.exists()) FileInputStream(archivo).use { load(it) }
}
fun datoFirma(variable: String, propiedad: String): String? =
    System.getenv(variable)?.takeIf { it.isNotEmpty() } ?: propiedadesFirma.getProperty(propiedad)
val hayFirma = propiedadesFirma.getProperty("storeFile") != null

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.sistemadual.panel_dueno"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.sistemadual.panel_dueno"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hayFirma) {
            create("release") {
                storeFile = file(propiedadesFirma.getProperty("storeFile"))
                storePassword = datoFirma("ANDROID_KEYSTORE_PASSWORD", "storePassword")
                keyAlias = datoFirma("ANDROID_KEY_ALIAS", "keyAlias")
                keyPassword = datoFirma("ANDROID_KEY_PASSWORD", "keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName(if (hayFirma) "release" else "debug")
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

dependencies {
    // Temas Theme.AppCompat.* de styles.xml (requisito de local_auth).
    implementation("androidx.appcompat:appcompat:1.7.1")
}
