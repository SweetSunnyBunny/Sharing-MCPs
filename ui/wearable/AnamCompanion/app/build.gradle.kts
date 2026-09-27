import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val anamApiKey: String = (localProps.getProperty("anamApiKey") ?: "")
val anamBaseUrl: String = (localProps.getProperty("anamBaseUrl") ?: "https://YOUR-ANAM-HOST")
fun javaString(value: String): String = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\""

android {
    namespace = "org.example.anam.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "org.example.anam.companion"
        minSdk = 26
        targetSdk = 34
        versionCode = 5
        versionName = "0.5-watch-only"

        buildConfigField("String", "ANAM_API_KEY", javaString(anamApiKey))
        buildConfigField("String", "ANAM_BASE_URL", javaString(anamBaseUrl))

    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
}
