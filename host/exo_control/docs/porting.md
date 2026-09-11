# Build, package and move the SDK

## Copying into another repository

Copy **the entire `host/exo_control/` directory**, excluding its local `build/`
directory. It has no parent-repository build paths, no Python runtime dependency,
and no vendor submodule dependency. The canonical protocol snapshot is included
under `proto/`; retain `SYNAPSE_API_COPYRIGHT` and `manifest.json`. The snapshot
preserves upstream's copyright; it does not grant additional redistribution
rights. Keep dependency notices with distributed binaries. No license for the
parent project has been invented by this SDK.

When updating against this repository, run from the SDK folder:

```sh
python scripts/sync_protocol.py --repo /path/to/ScienceXYZ
# Only after reviewing an intentional protocol change:
python scripts/sync_protocol.py --repo /path/to/ScienceXYZ --update
```

The check normalizes Git line endings and detects missing/changed/obsolete
schemas. The copy can build in isolation. This library requires a deployed
hub-manager matching the included protocol, with its control/state/command_result
Taps exposed. It never invokes an installed CLI, deploys the App or opens host USB.

## Windows x64

Prerequisites: VS 2022 C++ tools, CMake >=3.22, Ninja, JDK (for JNI), vcpkg.
Use VS Developer PowerShell and set JAVA_HOME to the JDK. Dependency installation
is separate from SDK compilation. Run these from a directory without a vcpkg
manifest when using the classic install commands (the pinned 2024 vcpkg does
not accept a `--classic` switch):

```powershell
$env:VCPKG_ROOT = 'C:/path/to/vcpkg'
& "$env:VCPKG_ROOT/vcpkg.exe" install grpc cppzmq protobuf --triplet x64-windows-static --host-triplet x64-windows
```

From the SDK folder:

```powershell
./scripts/build-sdk.ps1 -VcpkgRoot $env:VCPKG_ROOT -Platform Windows
javac --release 8 -d build/java android/src/main/java/org/sciencexyz/exo/ExoClient.java tests/JniSmoke.java
java '-Djava.library.path=build/Windows' -cp build/java JniSmoke
```

If host `grpc_cpp_plugin.exe` is missing, install `grpc[codegen]:x64-windows`.
The script uses host tools from installed/x64-windows/tools. A local static CRT
triplet is matched with /MT. Shared SDK output is `exo_control.dll` plus
`exo_jni.dll`; dependencies are statically linked with this triplet. Both DLLs
must be discoverable at runtime. The Java class loads exo_control before exo_jni
because Windows does not use java.library.path to resolve a DLL's dependencies.
The resulting C ABI can be loaded with LoadLibrary or linked using its import
library and `include/exo_control/exo.h`. No compiler-specific C++ objects cross
that ABI. Verify dependencies with `dumpbin /dependents` after changing triplets.

## Linux and macOS

Use platform-native toolchains: macOS builds must run on a Mac with Xcode command
line tools; Windows DLLs and Linux .so files are not macOS binaries. On macOS,
install cmake, ninja, protobuf, grpc and cppzmq with your package manager. On
Debian-like Linux install cmake, g++, libprotobuf-dev, protobuf-compiler,
libgrpc++-dev, protobuf-compiler-grpc and cppzmq-dev. Then:

```sh
cmake -S . -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel 6
ctest --test-dir build/native --output-on-failure
cmake --install build/native --prefix "$PWD/build/sdk"
```

Add `-DEXO_BUILD_JNI=ON` and JAVA_HOME for desktop Java. macOS produces
libexo_control.dylib (and libexo_jni.dylib with JNI); Linux produces .so files.
Build separately for x86_64/arm64 with matching dependencies. For a portable Mac
bundle, use static third-party dependencies or ship their dylibs and fix/verify
their install names with your normal packaging/signing process. The default
package-manager dynamic build is not a standalone binary distribution. macOS
build commands are supplied, but no Mac execution is claimed from this Windows
workspace.

## Android arm64-v8a

Prerequisites: Android NDK, CMake, Ninja, JDK, target dependencies and matching
host code generators. The local build uses NDK 27.2.12479018, Android API 26 and
c++_static with hidden implementation symbols. Do not reuse Windows libraries for Android. From outside a vcpkg
manifest directory, install:

```powershell
$env:ANDROID_NDK_HOME = 'C:/path/to/Android/Sdk/ndk/27.2.12479018'
# Pinned vcpkg/OpenSSL on Windows can race while renaming its Makefile during
# parallel install. Build that package serially first, then restore parallelism.
$env:VCPKG_MAX_CONCURRENCY = '1'
& "$env:VCPKG_ROOT/vcpkg.exe" install openssl --triplet arm64-android --host-triplet x64-windows
$env:VCPKG_MAX_CONCURRENCY = '8'
& "$env:VCPKG_ROOT/vcpkg.exe" install grpc cppzmq protobuf --triplet arm64-android --host-triplet x64-windows
```

Then from the SDK directory:

```powershell
./scripts/build-sdk.ps1 -Platform Android -VcpkgRoot $env:VCPKG_ROOT -Ndk $env:ANDROID_NDK_HOME
python scripts/package-android.py --build build/Android --ndk "$env:ANDROID_NDK_HOME" --dependency-share "$env:VCPKG_ROOT/installed/arm64-android/share" --output build/exo-control-arm64.aar
python scripts/verify-aar.py build/exo-control-arm64.aar
```

The script makes no device calls. The AAR contains Java classes, keep rules,
manifest INTERNET permission, two native SDK libraries and dependency notices.
It strips debug symbols from temporary copies; the original build libraries
retain symbols for diagnostics. Use --keep-debug for an unstripped AAR.
In another Android repository copy the AAR to `app/libs/` and add:

```kotlin
dependencies { implementation(files("libs/exo-control-arm64.aar")) }
```

Use minSdk >=26 and arm64-v8a. An x86_64 emulator needs its own x64-android build;
do not relabel the arm64 libraries. This build keeps a private static C++ runtime
inside each library and exports only the 14 C functions and three JNI entrypoints.
Only primitive values, caller-owned byte buffers and an opaque handle cross the
boundary; no C++ objects, exceptions, STL containers or allocation ownership do.
It therefore does not package/interpose libc++_shared.so from your Android app.
Do not expand that boundary to C++ objects while retaining isolated runtimes.
A custom shared-STL build can use package-android.py --shared-runtime, but must
use a runtime that passes verify-aar.py (the local NDK 27 runtime does not).
Run Android's
normal APK/AAB alignment and 16 KB page-size compatibility checks for your AGP
version. The SDK linker requests 16 KB load/RELRO alignment using both max-page-size
and common-page-size, per [Android's NDK r27 guidance](https://developer.android.com/guide/practices/page-sizes).

Alternatively copy this SDK as source, include `:exo` in settings.gradle.kts
with its projectDir pointing to `exo_control/android`, copy the built .so files
to android/src/main/jniLibs/arm64-v8a, and depend on project(":exo"). The included
Gradle library module uses the consuming project's Android Gradle plugin. It
defaults to compileSdk 35; adjust to your application's installed SDK.

For direct externalNativeBuild integration, point CMake at the SDK CMakeLists
and pass EXO_PROTOC/EXO_GRPC_PLUGIN **host** executable paths, the vcpkg
toolchain, arm64-android triplet, and NDK chainload toolchain just as in
scripts/build-sdk.ps1. On macOS/Linux, use the corresponding native host triplet
and generator paths without .exe. NDK-generated defaults do not substitute for
the target protobuf/gRPC/ZeroMQ dependencies. Keep all native targets on one NDK
and a consistent C++ ABI. The prebuilt AAR route avoids rebuilding gRPC in Android Studio.

## Calling the library

All methods are synchronous. Own the Java instance on one background executor;
create and close it there too. A UI button submits work to that executor. Poll
state periodically there while idle and post display updates to the UI thread.
An application lifecycle close must queue behind pending work and report errors.
Do not rely on a Java finalizer or stop the executor before close runs.

```java
// On the owning worker; host comes from your application's connection settings.
ExoClient client = new ExoClient(host, 647, 5000);
try {
    client.connect();                       // network protocol only
    client.setMode(ExoClient.CONNECTED);     // operator-requested USB handshake
    String result = client.query("version");
    String snapshot = client.pollState(100); // firmware text is asynchronous
    // Surface NativeException.status/message/resultJson on failure.
    // Only a supervised operator should request EXTERNAL/DECODE/poses/raw.
} finally {
    client.close();                         // bounded OFF, then close; may throw
}
```

Mode constants: OFF=1, EXTERNAL=2, DECODE=3, CONNECTED=4. Joint constants:
THUMB=1, INDEX=2, MIDDLE=3, RING=4, PINKY=5, WRIST=6. Pose values are integers
[-100,100] with -100 extension, 0 rest, +100 flex; omitted joints hold their
previous targets. The SDK does not change the device's motion/raw flags.

C callers use `exo_create`, `exo_connect`, typed mode/pose/query methods,
`exo_poll`, `exo_disconnect`, then `exo_destroy`. Every operation returns an
exo_status. Copy error/result JSON before issuing the next operation; that next
operation replaces them. Copy routines support a size query and never truncate.
Status values are OK=0, INVALID_ARGUMENT=1, FAILURE=2, DEVICE_REJECTED=3,
BUFFER_TOO_SMALL=4 and TIMEOUT=5. A command TIMEOUT leaves its outcome unknown;
do not blindly retry. Use `-DBUILD_SHARED_LIBS=OFF -DEXO_BUILD_JNI=OFF` for a
static C/C++ SDK; JNI uses the shared build.
State is full protobuf JSON (64-bit values are strings under protobuf JSON
rules); default-valued fields may be omitted. A state snapshot is not a
request-correlated firmware result. C pointer ownership must be serialized; do
not destroy a handle concurrently or call it after destruction.

C++ callers use `exo_control::Controller`, `make_synapse_transport` and the
generated mode/joint enums. Catch `CommandError` for the full device rejection.
The optional state callback is driven by poll/command waits. A standalone
CMake consumer can use the installed SDK:

```cmake
find_package(ExoControl CONFIG REQUIRED) # C ABI only, no dependency packages needed
target_link_libraries(my_c_app PRIVATE exo::control) # stable C ABI
find_package(ExoControl CONFIG REQUIRED COMPONENTS Core) # resolves C++ dependencies
target_link_libraries(my_cpp_app PRIVATE exo::core)  # C++ + generated messages
```

Supply CMAKE_PREFIX_PATH pointing to the SDK install prefix and (for Core) its
matching dependencies. The C++ package includes generated headers and archives but still
requires ABI-compatible protobuf/gRPC/ZeroMQ packages. Prefer the C ABI for
binary distribution; a Windows C++ consumer of the static-CRT archives must
also use /MT (`CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded` for Release). Use the C ABI for
binary reuse across compilers or dependency versions. A shared C ABI consumer
needs only exo.h, the DLL/import library (or .so/.dylib), and its runtime
dependencies. Static SDK builds also resolve the Core dependencies.

## Acceptance

Tests are in-memory or localhost-only; no test invokes synapsectl or hardware.
Before production use, the operator must validate the same device/App firmware
combination as gui-exo, version/limits/state display, gated rejection, a bounded
supervised pose, and OFF/reconnect behavior on the actual Android and desktop
clients. The user reported working Python gui-exo motion on 2026-09-11; this is
not bench acceptance of the new native library.
