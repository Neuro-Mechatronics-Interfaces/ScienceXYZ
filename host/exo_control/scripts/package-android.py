"""Assemble a standard prebuilt AAR from a successful arm64 NDK build.

No Gradle or device needed. Requires javac/jar from a JDK on PATH. Fails rather
than overwriting outputs. Default build uses private static C++ runtimes.
"""
import argparse
from pathlib import Path
import subprocess
import shutil
import tempfile
import zipfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--ndk", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--keep-debug", action="store_true", help="Keep native debug symbols in AAR (large)")
    p.add_argument("--shared-runtime", action="store_true", help="Include libc++_shared for a custom shared-STL build")
    p.add_argument("--dependency-share", type=Path, required=True,
                   help="vcpkg installed/arm64-android/share for dependency notices")
    args = p.parse_args()
    sdk = Path(__file__).resolve().parents[1]
    libraries = {name: args.build / name for name in ("libexo_control.so", "libexo_jni.so")}
    if args.shared_runtime:
        runtimes = list(args.ndk.glob("toolchains/llvm/prebuilt/*/sysroot/usr/lib/aarch64-linux-android/libc++_shared.so"))
        if len(runtimes) != 1:
            raise SystemExit("expected exactly one arm64 libc++_shared.so in NDK")
        libraries["libc++_shared.so"] = runtimes[0]
    for name, path in libraries.items():
        data = path.read_bytes()
        if data[:4] != b"\x7fELF" or int.from_bytes(data[18:20], "little") != 183:
            raise SystemExit(f"{name} is not an AArch64 ELF library")
    if args.output.exists():
        raise SystemExit("output exists; choose a new artifact name")
    notices = list(args.dependency_share.glob("*/copyright"))
    if not notices:
        raise SystemExit("no dependency copyright files found")
    with tempfile.TemporaryDirectory(prefix="exo-aar-") as tmp:
        temp = Path(tmp)
        if not args.keep_debug:
            strip_tools = list(args.ndk.glob("toolchains/llvm/prebuilt/*/bin/llvm-strip"))
            strip_tools += list(args.ndk.glob("toolchains/llvm/prebuilt/*/bin/llvm-strip.exe"))
            if len(strip_tools) != 1:
                raise SystemExit("expected one llvm-strip in NDK")
            stripped = {}
            for name, path in libraries.items():
                target = temp / name
                shutil.copy2(path, target)
                subprocess.run([str(strip_tools[0]), "--strip-unneeded", str(target)], check=True)
                stripped[name] = target
            libraries = stripped
        classes = temp / "classes"
        classes.mkdir()
        java = sdk / "android/src/main/java/org/sciencexyz/exo/ExoClient.java"
        subprocess.run(["javac", "--release", "8", "-d", str(classes), str(java)], check=True)
        jar = temp / "classes.jar"
        subprocess.run(["jar", "cf", str(jar), "-C", str(classes), "."], check=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as out:
            out.write(jar, "classes.jar")
            out.writestr("AndroidManifest.xml", '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.sciencexyz.exo"><uses-sdk android:minSdkVersion="26"/><uses-permission android:name="android.permission.INTERNET"/></manifest>')
            out.write(sdk / "android/consumer-rules.pro", "proguard.txt")
            for name, path in libraries.items():
                out.write(path, f"jni/arm64-v8a/{name}")
            out.write(sdk / "proto/SYNAPSE_API_COPYRIGHT", "assets/exo-notices/SYNAPSE_API_COPYRIGHT")
            for notice in notices:
                out.write(notice, f"assets/exo-notices/{notice.parent.name}.txt")
            for notice in args.ndk.glob("toolchains/llvm/prebuilt/*/NOTICE"):
                out.write(notice, "assets/exo-notices/NDK-NOTICE.txt")
            if (args.ndk / "NOTICE").is_file():
                out.write(args.ndk / "NOTICE", "assets/exo-notices/NDK-ROOT-NOTICE.txt")
            if (args.ndk / "NOTICE.toolchain").is_file():
                out.write(args.ndk / "NOTICE.toolchain", "assets/exo-notices/NDK-TOOLCHAIN-NOTICE.txt")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
