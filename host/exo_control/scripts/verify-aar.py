"""Check packaged arm64 ELF architecture/alignment and Java classes, offline."""
import argparse
import io
import struct
import zipfile
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aar")
    args = parser.parse_args()
    with zipfile.ZipFile(args.aar) as aar:
        if aar.testzip() is not None:
            raise SystemExit("AAR CRC check failed")
        manifest = ET.fromstring(aar.read("AndroidManifest.xml"))
        android = "{http://schemas.android.com/apk/res/android}"
        if manifest.find("uses-sdk").get(android + "minSdkVersion") != "26":
            raise SystemExit("unexpected minimum SDK")
        if not any(p.get(android + "name") == "android.permission.INTERNET" for p in manifest.findall("uses-permission")):
            raise SystemExit("missing INTERNET permission")
        with zipfile.ZipFile(io.BytesIO(aar.read("classes.jar"))) as jar:
            for cls in ("ExoClient", "ExoClient$NativeException"):
                if f"org/sciencexyz/exo/{cls}.class" not in jar.namelist():
                    raise SystemExit(f"missing Java class {cls}")
        libraries = ["libexo_control.so", "libexo_jni.so"]
        if "jni/arm64-v8a/libc++_shared.so" in aar.namelist():
            libraries.append("libc++_shared.so")
        for name in libraries:
            data = aar.read("jni/arm64-v8a/" + name)
            if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
                raise SystemExit(f"{name}: expected little-endian ELF64 AArch64")
            offset = struct.unpack_from("<Q", data, 32)[0]
            stride, count = struct.unpack_from("<HH", data, 54)
            loads = relros = 0
            load_segments = []
            dynamic = None
            for i in range(count):
                kind, flags, file_offset, address, physical, file_size, memory_size, alignment = struct.unpack_from("<IIQQQQQQ", data, offset + i * stride)
                if kind == 1:
                    loads += 1
                    load_segments.append((address, file_offset, file_size))
                    if alignment < 16384 or (address - file_offset) % 16384:
                        raise SystemExit(f"{name}: LOAD not 16 KB aligned")
                if kind == 0x6474E552:
                    relros += 1
                    if (address + memory_size) % 16384:
                        raise SystemExit(f"{name}: RELRO end not 16 KB aligned")
                if kind == 2:
                    dynamic = (file_offset, file_size)
            if not loads or not relros:
                raise SystemExit(f"{name}: missing LOAD/RELRO")
            if dynamic is None:
                raise SystemExit(f"{name}: missing dynamic section")
            needed = []
            strings_address = None
            for pos in range(dynamic[0], dynamic[0] + dynamic[1], 16):
                tag, value = struct.unpack_from("<qQ", data, pos)
                if tag == 0:
                    break
                if tag == 1:
                    needed.append(value)
                if tag == 5:
                    strings_address = value
            strings_offset = next((off + strings_address - addr for addr, off, size in load_segments
                                   if strings_address is not None and addr <= strings_address < addr + size), None)
            if strings_offset is None:
                raise SystemExit(f"{name}: missing dynamic string table")
            allowed = set(libraries) | {"libc.so", "libm.so", "libdl.so", "libandroid.so", "liblog.so"}
            for relative in needed:
                start = strings_offset + relative
                dependency = data[start:data.index(b"\0", start)].decode("ascii")
                if dependency not in allowed:
                    raise SystemExit(f"{name}: unbundled dependency {dependency}")
            print(f"{name}: AArch64; {loads} LOAD and {relros} RELRO segments aligned")
    print("AAR structure and ELF checks passed; not an Android runtime test")


if __name__ == "__main__":
    main()
