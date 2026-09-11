# Android and desktop buildability audit

Inspected 2026-09-11: `vendor/synapse-cpp` revision
`4479dedbc317294fd2256c1b9554ae36b880374d`, canonical API revision
`4c9e9c43a42d2980bdc4238c72568d52adf5a8be`. The working tree's new raw command is
included in the SDK snapshot; `proto/manifest.json` records exact normalized
content hashes, including changes not yet committed in the parent repository.

The existing Synapse C++ client requires C++17, gRPC, protobuf and cppzmq
(header wrapper over libzmq). gRPC additionally pulls Abseil, c-ares, OpenSSL,
RE2, upb/utf8-range and zlib in this vcpkg revision. There is no host USB or
serial requirement. Android bionic supports the relevant sockets and threads.
The multicast discovery source directly includes POSIX headers and does not
compile unchanged on Windows; this SDK instead accepts a host and uses ListTaps.

The upstream top-level CMake generates bindings into its own include directory
and does not separate host `protoc`/`grpc_cpp_plugin` from target executables.
Tap discovery uses Device::query without a deadline. Tap socket shutdown has
no configured linger limit, and `Device` expects its RPC URI to include a port.
These are concrete reasons not to link the upstream Tap class into a bounded
mobile client unchanged. No vendor source was modified. The small
`src/synapse_transport.cpp` implements the same ListTaps RPC and single-frame
ZeroMQ PUB/SUB protocol using generated **canonical** API bindings, with RPC
deadlines, strict Tap directions, advertised TCP port discovery, and zero linger.
It never hard-codes peripheral IDs or Tap ports. Hostname/IP and RPC port are
separate parameters; the protocol's default RPC port is 647.

Decision: the controller is portable; the work is primarily dependency/toolchain
integration, not a USB port. libzmq is not the blocking dependency. The largest
build is gRPC and its transitive libraries. Host generators must match the
target protobuf version. All generated files go into the build directory.

Local package manager baseline: vcpkg
`91f002cae2281636da5155efc5a11d67efa72415`, protobuf 4.25.1 (`protoc` 25.1),
gRPC 1.60.0, cppzmq 4.10.0, libzmq 4.3.5. The manifest deliberately does not
upgrade a consuming repository's dependency baseline. Pin that revision for
reproduction, or validate upgrades in your application's existing vcpkg baseline.

References: [NDK CMake guide](https://developer.android.com/ndk/guides/cmake),
[Gradle native integration](https://developer.android.com/studio/projects/gradle-external-native-builds),
[vcpkg gRPC dependencies](https://vcpkg.io/en/package/grpc.html),
[JNI library loading](https://github.com/android/ndk/wiki/JNI).

## Semantics and limits

`connect()` opens only the three Exo control-plane Taps, retries GET_STATE during
the PUB/SUB slow-joiner interval, and waits for an acknowledged subscription.
It does not engage the USB link. The caller explicitly chooses CONNECTED,
EXTERNAL or DECODE. Reply correlation requires both request ID and command kind;
ACCEPTED is nonterminal. Commands other than the read-only handshake are never
retried. A timeout means outcome unknown, not rejected. There is no automatic
reconnect, mode restoration, raw replay, heartbeat or resampling.

The controller is synchronous and single-owner. While waiting it processes
state; while idle the caller must `poll()`. State callbacks run on the owner
thread, cannot reenter/destroy the controller, and should not throw or block.
Protobuf state includes the original device timestamp, state version and full
Exo status. Snapshots are not an event log. PUB/SUB can drop messages when a
subscriber is absent or queues fill; command timeouts expose missing terminal
replies, but state completeness is not guaranteed. `connected()` means a session
was established, not that the peer is currently reachable.

`state.exo.last_reply` is asynchronous shared device state, not a per-request
return value or measured movement completion. Retain the result's request ID
and state version in application logs. Firmware text may arrive after the
terminal result; continue polling. Do not authorize operations from the known
stale `exo.configured` broadcast field. Device motion/raw flags remain the gate.

OFF is attempted after any possibly engaging mode/pose/raw request, including a
timeout. Explicit close reports a failed OFF, then closes sockets anyway. The
destructor is a last-resort fallback that cannot report failure. A transport-only
connection does not disarm another client's hand on close. Multiple simultaneous
operators are not arbitrated: use one supervised owner. Process death/network
loss can prevent OFF; device watchdog behavior is outside this client. No stop,
start, deploy, configuration or raw USB operation is exposed by this SDK.

All waits are bounded by configured timeouts except caller-supplied callbacks
and OS thread scheduling. Connect can use up to three timeout windows (discovery,
handshake, subscription); disconnect uses one command timeout. Raw lines are
bounded to 200 UTF-8 bytes, reject CR/LF/NUL and blank input; this matches the
device's byte-oriented limit and is stricter than Python's Unicode character count.

## Verification and artifacts (2026-09-11)

| Target | Evidence |
| --- | --- |
| Windows x64 | MSVC 19.41, static vcpkg dependencies; DLL, JNI DLL and Java JAR built. All 3 CTests pass: controller, localhost gRPC/ZeroMQ/C ABI, JVM load/errors/owner-thread/close. |
| Linux x64 (WSL) | GCC 15.2, protobuf 3.21.12, gRPC 1.51.1, libzmq 4.3.5. Both shared and static builds pass their 2 CTests. |
| Repository transfer | Copied SDK directory builds a Windows DLL independently. Installed C SDK consumed by a separate Windows C project without dependency packages; installed C++ SDK consumed by a separate Linux C++ project. |
| Android arm64-v8a | NDK 27.2.12479018, API 26, private c++_static; all dependencies and SDK/JNI cross-compiled and linked. Release AAR passes CRC, Java-class/manifest, AArch64, LOAD/RELRO 16 KB alignment and bundled-dependency checks. |
| macOS | Build/install instructions and CI template supplied; no Mac host available, so not compiled or runtime-tested here. |

The Android shared runtime initially supplied by NDK 27 had a RELRO end aligned
to 4 KB despite 16 KB LOAD alignment. `verify-aar.py` caught this. The final build
uses private static runtimes, default-hidden implementation visibility, and
`--exclude-libs,ALL`; dynamic symbol inspection confirms exactly 14 C ABI exports
from libexo_control.so and 3 JNI exports from libexo_jni.so, with no C++ runtime
symbols exposed. All cross-library calls use the C interface, and allocation,
exceptions and C++ ownership stay in their creating library. The AAR requires
only Android system libraries and its own libexo_control.so. No shared C++ runtime
is bundled or substituted in a consuming application.

Final local artifacts, relative to ScienceXYZ (build outputs are Git-ignored):

- `build-win/exo-packages/exo-control-0.1.0-android-arm64.aar`
- `build-win/exo-packages/exo-control-0.1.0-win64.zip`
- `build-win/exo-packages/exo-control-0.1.0-source.zip`

Reproducible SDK source is `host/exo_control/`; Windows install prefix is
`build-win/exo-control/install`, Android prefix is `build-win/exo-android/install`.
The AAR strips only temporary copies; unstripped native libraries remain in the
Android build directory. Windows package includes headers, archives/import libs,
DLLs, JAR/Java source, CMake package metadata, protocol provenance and notices.
Use `scripts/build-sdk.ps1`, `scripts/package-android.py`, `scripts/verify-aar.py`
and the porting guide to reproduce them. Schema parity check passed for 27 files.

Not performed: native Android JVM execution, consuming APK/AAB build,
macOS execution, or native-client device/physical-motion acceptance. No agent
device commands, synapsectl, adb, flash, deployment or actuation were run.
The user reported working Python gui-exo motion on 2026-09-11; that evidence
does not certify the new native SDK. T-62 retains native-client bench acceptance;
T-53/T-57 retain their broader existing safety/hardware scope.
