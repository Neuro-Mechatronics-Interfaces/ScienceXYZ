// Included from an existing Android Studio project; use that project's AGP.
plugins { id("com.android.library") }

android {
    namespace = "org.sciencexyz.exo"
    compileSdk = 35
    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
        ndk { abiFilters += listOf("arm64-v8a") }
    }
    // Prebuilt SDK .so files go under src/main/jniLibs/arm64-v8a.
    // Java remains source so there is no dependency on a separately built JAR.
}
