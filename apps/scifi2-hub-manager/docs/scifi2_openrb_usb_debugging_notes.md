# SciFi-2 ↔ OpenRB-150 USB CDC Debugging Notes

## Implementation status (2026-09-10)

These operator notes were supplied during the rename to
`apps/scifi2-hub-manager`. The current diagnostic lives in `src/main.cpp`,
`scifi2_hub::SciFi2HubManagerApp::setup()`, and logs real/effective UID/GID,
enumeration errors, VID/PID/bus/address, and open success/failure. A matching
device is logged as `Found candidate OpenRB-150 (VID/PID match)`.

The reported clean build failed before compiling the App: the pinned libusb
port ran `autoreconf -vfi` but `aclocal` was absent. Docker now installs
`automake` for both build architectures. After adding it in the cached SDK
container, vcpkg built libusb 1.0.27 and the complete renamed App compiled and
linked for ARM64. This is build evidence, not App-process access or CDC transfer
acceptance. No agent ran `synapsectl` or changed headstage permissions.

The operator's next clean-build command from the repository root is:

```bash
synapsectl apps build --clean apps/scifi2-hub-manager
```

The kernel/ADB observations below do not establish App access. The current Exo
backend expects a tty, which the reported kernel does not provide. Interface
claiming, dual-CDC endpoint/control handling, safe transport teardown, and
actuator acceptance remain future work; opening a libusb handle implements none
of these. Temporary root/permission experiments below are historical operator
steps, not application behavior or a production permission policy.

## Goal

Determine whether a **ROBOTIS OpenRB-150** can be connected directly to a **Science SciFi-2 headstage** over USB:

```text
SciFi-2 <-- USB --> OpenRB-150
```

without requiring the Via devkit / Lattice FPGA / Axon transport.

The intended software path is eventually:

```text
Synapse App
   |
   | libusb or CDC-ACM
   v
SciFi-2 Linux USB host
   |
   | USB
   v
OpenRB-150
```

---

## 1. Confirm ADB is connected to the SciFi-2

```cmd
adb devices
```

Observed:

```text
List of devices attached
25aeaaaa        device
```

Confirm the target:

```cmd
adb shell uname -a
```

Observed:

```text
Linux scifi-1 5.4.233 #1 SMP PREEMPT Mon Feb 9 22:42:28 UTC 2026 aarch64 aarch64 aarch64 GNU/Linux
```

The SciFi-2 is therefore running an AArch64 Linux 5.4.233 kernel and exposes `adbd`.

---

## 2. `synapsectl file` is NOT the real Linux root filesystem

This does **not** expose `/dev`:

```cmd
synapsectl -u "192.168.100.157" file ls /dev
```

Observed:

```text
Failed to list directory: [Errno 2] No such file
```

The SFTP root exposed by:

```cmd
synapsectl -u "192.168.100.157" file ls /
```

contains only a restricted namespace such as:

```text
disk_writer/
models/
sdcard/
nml-diag-report.txt
```

Therefore:

> `synapsectl file` is a restricted SFTP namespace and cannot be used as the equivalent of `ls /dev` on the underlying SciFi-2 Linux host.

ADB **can** inspect the actual `/dev`.

---

## 3. Initial USB debugging while physically attached by USB was misleading

Initial checks showed only the USB root hub:

```cmd
adb shell "ls -1 /sys/bus/usb/devices"
```

Observed:

```text
1-0:1.0
usb1
```

And:

```cmd
adb shell "for d in /sys/bus/usb/devices/*; do [ -f \"$d/idVendor\" ] && echo -n \"$d \" && cat \"$d/idVendor\" \"$d/idProduct\" | tr '\n' ' ' && echo; done"
```

Observed:

```text
/sys/bus/usb/devices/usb1 1d6b 0002
```

`1d6b:0002` is the Linux USB 2.0 root hub.

Important observation:

> When SciFi-2 is connected by USB for charging / wired ADB, its peripheral functionality is disabled as a patient-safety behavior. Therefore wired ADB can alter the USB/peripheral state being tested.

---

## 4. Move ADB to TCP/IP / Wi-Fi

Enable ADB over TCP while wired ADB is still available:

```cmd
adb tcpip 5555
```

Connect using the SciFi-2 network address:

```cmd
adb connect 192.168.100.157:5555
```

Verify:

```cmd
adb devices
```

Expected / observed network target:

```text
192.168.100.157:5555    device
```

Then physically disconnect the SciFi-2 USB cable from the PC.

Use the network target explicitly if needed:

```cmd
adb -s 192.168.100.157:5555 shell uname -a
```

---

## 5. OpenRB-150 DOES enumerate on the SciFi-2 USB host

With wired ADB removed and the OpenRB-150 plugged into the SciFi-2, kernel logging showed:

```text
usb 2-1.3: new full-speed USB device number 12 using xhci-hcd
usb 2-1.3: New USB device found, VID=2f5d, PID=2202
```

This proves:

```text
SciFi-2 xHCI host     OK
USB physical link     OK
OpenRB enumeration    OK
VID/PID discovery     OK
```

The OpenRB enumerated as:

```text
VID = 2f5d
PID = 2202
```

---

## 6. Inspect USB topology

```cmd
adb shell "ls -1 /sys/bus/usb/devices"
```

With the OpenRB connected, relevant entries include:

```text
2-1.3
2-1.3:1.0
2-1.3:1.1
2-1.3:1.2
2-1.3:1.3
```

This indicates one USB device with four interfaces.

---

## 7. Confirm OpenRB identity

```cmd
adb shell "for f in manufacturer product serial idVendor idProduct bDeviceClass; do echo -n $f=; cat /sys/bus/usb/devices/2-1.3/$f 2>/dev/null; done"
```

Observed:

```text
manufacturer=ROBOTIS
product=OpenRB-150
serial=7474312E503059384C2E3120FF092F327474312E503059384C2E3120FF092F32
idVendor=2f5d
idProduct=2202
bDeviceClass=ef
```

So the SciFi-2 definitively identifies the connected device as:

```text
ROBOTIS OpenRB-150
VID:PID = 2f5d:2202
```

---

## 8. Inspect OpenRB USB interface classes and driver binding

```cmd
adb shell "for d in /sys/bus/usb/devices/2-1.3:1.*; do echo === $d ===; echo -n class=; cat $d/bInterfaceClass; echo -n subclass=; cat $d/bInterfaceSubClass; echo -n protocol=; cat $d/bInterfaceProtocol; echo -n driver=; readlink $d/driver 2>/dev/null || echo NONE; done"
```

Observed:

```text
=== /sys/bus/usb/devices/2-1.3:1.0 ===
class=02
subclass=02
protocol=00
driver=NONE

=== /sys/bus/usb/devices/2-1.3:1.1 ===
class=0a
subclass=00
protocol=00
driver=NONE

=== /sys/bus/usb/devices/2-1.3:1.2 ===
class=02
subclass=02
protocol=00
driver=NONE

=== /sys/bus/usb/devices/2-1.3:1.3 ===
class=0a
subclass=00
protocol=00
driver=NONE
```

Interpretation:

```text
1.0  class 02 / subclass 02   CDC Communications / ACM control
1.1  class 0a                 CDC Data

1.2  class 02 / subclass 02   second CDC Communications / ACM control
1.3  class 0a                 second CDC Data
```

All interfaces report:

```text
driver=NONE
```

So the OpenRB is exposing CDC-style interfaces correctly, but no host driver is binding to them.

---

## 9. No `/dev/ttyACM*` appears

```cmd
adb shell "ls -l /dev/ttyACM* 2>/dev/null"
```

Observed:

```text
<no output>
```

Therefore the OpenRB is not becoming `/dev/ttyACM0`.

---

## 10. SciFi-2 kernel lacks CDC-ACM support

Check kernel configuration:

```cmd
adb shell "zcat /proc/config.gz 2>/dev/null | grep -E 'CONFIG_USB_ACM|CONFIG_USB_SERIAL'"
```

Observed:

```text
# CONFIG_USB_ACM is not set
CONFIG_USB_SERIAL=y
# CONFIG_USB_SERIAL_CONSOLE is not set
# CONFIG_USB_SERIAL_GENERIC is not set
...
CONFIG_USB_SERIAL_CP210X=y
...
```

Critical result:

```text
# CONFIG_USB_ACM is not set
```

Therefore:

> The SciFi-2 kernel was built without the Linux `cdc_acm` host driver.

`CONFIG_USB_SERIAL=y` does **not** substitute for `CONFIG_USB_ACM`.

Check for a module:

```cmd
adb shell "ls /sys/module/cdc_acm 2>/dev/null"
```

Observed:

```text
<no output>
```

And:

```cmd
adb shell "find /lib/modules -type f \( -iname '*cdc-acm*' -o -iname '*cdc_acm*' \) 2>/dev/null"
```

Observed:

```text
<no output>
```

There is therefore no available `cdc_acm.ko` module either.

A file named:

```text
/lib/modules/5.4.233/extra/bolero_cdc_dlkm.ko
```

was found, but this is unrelated to USB CDC-ACM.

---

## 11. Raw USB device node exists

Kernel log assigned USB device number `12` on bus `2`.

Inspect:

```cmd
adb shell "ls -l /dev/bus/usb/002"
```

Observed:

```text
crw-rw-r-- 1 root root 189, 128 ... 001
crw-rw-r-- 1 root root 189, 129 ... 002
crw-rw-r-- 1 root root 189, 135 ... 008
crw-rw-r-- 1 root root 189, 137 ... 010
crw-rw-r-- 1 root root 189, 139 ... 012
```

Specific OpenRB node:

```cmd
adb shell "ls -l /dev/bus/usb/002/012"
```

Observed:

```text
crw-rw-r-- 1 root root 189, 139 ... /dev/bus/usb/002/012
```

And:

```cmd
adb shell "stat /dev/bus/usb/002/012"
```

showed:

```text
Access: (0664/crw-rw-r--)
Uid: root
Gid: root
```

Important:

> `/002/012` is temporary. Device number `012` can change after reconnect. Production code should discover the OpenRB by VID/PID (`2f5d:2202`) using libusb.

---

## 12. ADB user permissions

Initially:

```cmd
adb shell id
```

Observed:

```text
uid=2000(adb) gid=2000(adb) groups=2000(adb),1004,1007,1011,1015(sdcard),1028,3001,3002,3003(inet),3006
```

Read access:

```cmd
adb shell "test -r /dev/bus/usb/002/012 && echo READ_OK || echo READ_NO"
```

Observed:

```text
READ_OK
```

Write access:

```cmd
adb shell "if [ -w /dev/bus/usb/002/012 ]; then echo WRITE_OK; else echo WRITE_NO; fi"
```

Observed:

```text
WRITE_NO
```

This is consistent with:

```text
0664 root:root
```

because the non-root `adb` user receives read-only access through the `other` permission bits.

---

## 13. Root ADB proves raw USB write access is possible

Enable root ADB:

```cmd
adb root
```

Observed:

```text
restarting adbd as root
```

Verify network ADB remains available:

```cmd
adb devices
```

Observed:

```text
192.168.100.157:5555    device
```

Then:

```cmd
adb shell "if [ -w /dev/bus/usb/002/012 ]; then echo WRITE_OK; else echo WRITE_NO; fi"
```

Observed:

```text
WRITE_OK
```

Therefore the raw usbfs device node is usable for bidirectional communication when permissions permit.

---

## 14. Temporary permission override for testing

As root:

```cmd
adb shell "chmod 666 /dev/bus/usb/002/012"
```

Verify:

```cmd
adb shell "ls -l /dev/bus/usb/002/012"
```

Observed:

```text
crw-rw-rw- 1 root root 189, 139 ... /dev/bus/usb/002/012
```

This temporarily removes permissions as a confound for testing a Synapse App.

This is **diagnostic only**. The USB bus/device number and permissions can reset after reconnect or reboot.

---

## 15. libusb is already installed on the SciFi-2

```cmd
adb shell "ldconfig -p 2>/dev/null | grep libusb"
```

Observed:

```text
libusb-1.0.so.0 (libc6,AArch64) => /lib/aarch64-linux-gnu/libusb-1.0.so.0
```

And:

```cmd
adb shell "find /usr /lib -iname 'libusb-1.0.so*' 2>/dev/null"
```

Observed:

```text
/usr/lib/.debug/libusb-1.0.so.0.1.0
/usr/lib/aarch64-linux-gnu/libusb-1.0.so.0
/usr/lib/aarch64-linux-gnu/libusb-1.0.so.0.2.0
```

Therefore a Synapse App can potentially use the system's AArch64 `libusb` runtime.

---

## 16. Minimal libusb Synapse App diagnostic

Include:

```cpp
#include <libusb.h>  // include directory supplied by PkgConfig::LIBUSB
#include <unistd.h>
```

At setup, log the runtime identity:

```cpp
spdlog::info(
    "Synapse App runtime uid={} gid={}",
    static_cast<unsigned long>(getuid()),
    static_cast<unsigned long>(getgid())
);
```

Then enumerate USB devices and test opening the OpenRB:

```cpp
libusb_context* ctx = nullptr;

int rc = libusb_init(&ctx);
if (rc != 0) {
    spdlog::error("libusb_init failed: {}", libusb_error_name(rc));
} else {
    libusb_device** devices = nullptr;
    ssize_t count = libusb_get_device_list(ctx, &devices);

    if (count < 0) {
        spdlog::error(
            "libusb_get_device_list failed: {} ({})",
            libusb_error_name(static_cast<int>(count)),
            count
        );
    } else {
        spdlog::info("libusb sees {} USB devices", count);

        for (ssize_t i = 0; i < count; ++i) {
            libusb_device_descriptor desc{};

            if (libusb_get_device_descriptor(devices[i], &desc) != 0)
                continue;

            spdlog::info(
                "USB {:04x}:{:04x} bus={} addr={}",
                desc.idVendor,
                desc.idProduct,
                libusb_get_bus_number(devices[i]),
                libusb_get_device_address(devices[i])
            );

            if (desc.idVendor == 0x2f5d &&
                desc.idProduct == 0x2202) {

                spdlog::info("FOUND OpenRB-150");

                libusb_device_handle* handle = nullptr;
                int open_rc = libusb_open(devices[i], &handle);

                if (open_rc == 0) {
                    spdlog::info("SUCCESS: libusb_open(OpenRB-150)");
                    libusb_close(handle);
                } else {
                    spdlog::error(
                        "FAILED: libusb_open(OpenRB-150): {} ({})",
                        libusb_error_name(open_rc),
                        open_rc
                    );
                }
            }
        }
    }

    libusb_free_device_list(devices, 1);
    libusb_exit(ctx);
}
```

Discover and link libusb in CMake (the manifest declares the dependency):

```cmake
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)
target_link_libraries(scifi2-hub-manager PRIVATE PkgConfig::LIBUSB)
```

Expected success log:

```text
FOUND OpenRB-150
SUCCESS: libusb_open(OpenRB-150)
```

---

## 17. Current architecture conclusion

The following has now been demonstrated:

```text
SciFi-2 USB host                    YES
OpenRB-150 USB enumeration          YES
OpenRB VID/PID                      2f5d:2202
OpenRB CDC interfaces               YES
Raw usbfs device node               YES
libusb runtime on SciFi             YES
Raw USB write as root               YES
Linux cdc_acm kernel driver         NO
/dev/ttyACM0                        NO
Via / Lattice / Axon required       NOT for raw USB transport
Synapse App libusb access           NEXT TEST
```

Current likely path:

```text
Synapse App
     |
     | libusb
     v
/dev/bus/usb/...
     |
     | xHCI USB host
     v
OpenRB-150
```

rather than:

```text
Synapse App
     |
     | termios
     v
/dev/ttyACM0
     |
     v
OpenRB-150
```

because the SciFi-2 kernel currently has:

```text
# CONFIG_USB_ACM is not set
```

---

## 18. Useful repeatable ADB commands

### Connect wirelessly

```cmd
adb tcpip 5555
adb connect 192.168.100.157:5555
adb devices
```

### Confirm SciFi target

```cmd
adb shell uname -a
adb shell id
```

### Watch USB plug/unplug

```cmd
adb shell dmesg -w
```

### List USB topology

```cmd
adb shell "ls -1 /sys/bus/usb/devices"
```

### List VID/PID pairs

```cmd
adb shell "for d in /sys/bus/usb/devices/*; do [ -f \"$d/idVendor\" ] && echo -n \"$d \" && cat \"$d/idVendor\" \"$d/idProduct\" | tr '\n' ' ' && echo; done"
```

### Inspect OpenRB identity

```cmd
adb shell "for f in manufacturer product serial idVendor idProduct bDeviceClass; do echo -n $f=; cat /sys/bus/usb/devices/2-1.3/$f 2>/dev/null; done"
```

### Inspect USB interface classes and bound drivers

```cmd
adb shell "for d in /sys/bus/usb/devices/2-1.3:1.*; do echo === $d ===; echo -n class=; cat $d/bInterfaceClass; echo -n subclass=; cat $d/bInterfaceSubClass; echo -n protocol=; cat $d/bInterfaceProtocol; echo -n driver=; readlink $d/driver 2>/dev/null || echo NONE; done"
```

### Check for CDC ACM tty node

```cmd
adb shell "ls -l /dev/ttyACM* 2>/dev/null"
```

### Check kernel USB serial configuration

```cmd
adb shell "zcat /proc/config.gz 2>/dev/null | grep -E 'CONFIG_USB_ACM|CONFIG_USB_SERIAL'"
```

### Check for `cdc_acm`

```cmd
adb shell "ls /sys/module/cdc_acm 2>/dev/null"
adb shell "find /lib/modules -type f \( -iname '*cdc-acm*' -o -iname '*cdc_acm*' \) 2>/dev/null"
```

### Inspect raw usbfs nodes

```cmd
adb shell "ls -l /dev/bus/usb/002"
```

### Check libusb availability

```cmd
adb shell "ldconfig -p 2>/dev/null | grep libusb"
adb shell "find /usr /lib -iname 'libusb-1.0.so*' 2>/dev/null"
```

### Temporarily run ADB as root

```cmd
adb root
adb devices
```

### Temporary raw-USB permissions for a test

```cmd
adb shell "chmod 666 /dev/bus/usb/002/012"
adb shell "ls -l /dev/bus/usb/002/012"
```

Remember that `/002/012` is dynamic and must not be hard-coded into production code.

---

## Next test

Deploy the Synapse App containing the minimal `libusb` enumeration/open diagnostic while the OpenRB node is temporarily `0666`.

The decisive result is:

```text
FOUND OpenRB-150
SUCCESS: libusb_open(OpenRB-150)
```

If that succeeds, proceed to interface claiming, endpoint inspection, and a userspace CDC transport implementation.
