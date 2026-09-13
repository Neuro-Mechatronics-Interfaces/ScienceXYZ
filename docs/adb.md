# ADB Tips #

Before running any dumps, make sure terminal is in this repo!

```bash
cd C:/MyRepos/C/ScienceXYZ
```

## 1. Connect over WiFi ##

**As of 2026-09-13 the SciFi-2 is configured to start `adbd` in TCP mode on
port 5555 automatically at every boot** (`persist.adb.tcp.port=5555`, persisted
in `/etc/build.prop` via the image's `leprop` store; a fallback lives in
`/etc/default/adbd`). In normal operation you should be able to connect over the
network without touching USB:

```bash
adb connect 192.168.100.157:5555
adb devices
```

Once you see:

```text
192.168.100.157:5555    device
```

you are connected wirelessly.

### First-time / recovery: re-arm TCP mode from USB ###

If a firmware reflash or factory-reset clears the persistent property, re-arm it
once over USB:

```bash
adb devices          # confirm the USB device is visible
adb tcpip 5555        # -> "restarting in TCP mode port: 5555"
adb connect 192.168.100.157:5555
adb devices
```

Then unplug USB and continue wirelessly. To make it persist again across reboots
(so you do not need USB next time), while connected as root:

```bash
adb root
adb shell 'setprop persist.adb.tcp.port 5555'   # leprop persists this to /etc/build.prop
adb shell 'getprop persist.adb.tcp.port'        # confirm: 5555
```

## 2. Make sure you're in root ##

If you want root ADB again after that:

```bash
adb root
```

Note that this will only work *after* unplugging the USB connection.

---

## 3. TIME SYNCHRONIZATION ##

The SciFi-2 has **no working battery-backed RTC** (`hwclock` reads 1970), so it
loses the wall clock on every power-down. The bench LAN (`192.168.100.0/24`) is
**isolated**: it has no internet, the wireless router runs no NTP server and its
DHCP hands out no NTP server (option 42), and the router's own clock is stale.
The only correct clock on the segment is a host machine.

### Automatic (preferred): NTP from a host ###

The SciFi-2's stock `systemd-timesyncd` is configured
(`/etc/systemd/timesyncd.conf.d/10-bench-laptop.conf`) to sync from, in order:

1. `192.168.100.14` — Max's Windows laptop (default);
2. `192.168.100.106` — the M4 MacBook Pro (used only if `.14` is absent).

It syncs within ~32 s of a poll whenever one of those hosts is **serving NTP on
UDP 123**. To make a host serve NTP:

- **Windows laptop** — run the durable setup once, in an **elevated** PowerShell.
  It configures the built-in Windows Time service to discipline itself from the
  internet and serve the bench LAN, and opens the firewall:

  ```powershell
  # Administrator PowerShell
  ./scripts/setup_laptop_ntp_server.ps1
  ```

- **Windows laptop, auto-start the responder on boot** — if you want
  `bench_ntp_server.py` (rather than the Windows Time service) to be the server and
  come up automatically on every boot, install it as a Scheduled Task once, in an
  **elevated** PowerShell. It registers a boot task (runs as SYSTEM, restarts on
  failure), opens the firewall, and starts immediately:

  ```powershell
  # Administrator PowerShell
  ./scripts/install_bench_ntp_task.ps1                 # serves 192.168.100.14:123
  ./scripts/install_bench_ntp_task.ps1 -Uninstall      # remove it
  ```

  Keep Windows time sync on so the served clock stays accurate.

- **Any host (Windows or macOS), quick/manual** — run the cross-platform
  responder in a terminal while at the bench (needs privilege to bind UDP 123):

  ```bash
  sudo python scripts/bench_ntp_server.py --bind 192.168.100.14   # or .106 on the Mac
  ```

  Discipline the host clock first (Windows Time service on / macOS network time on)
  so the served time is accurate — the responder serves the host's own clock.

> **Router NTP field.** Setting the router's own NTP server to `192.168.100.14`
> typically fails with a connectivity warning: this router validates the NTP
> server over its (absent) internet/WAN path and never queries the LAN host, so it
> cannot sync its own clock from the laptop. This does not affect the SciFi-2,
> which reaches the laptop directly via the `timesyncd` config above. DHCP option
> 42 is optional and only a convenience; the static config does not depend on it.

To confirm the SciFi-2 synced:

```bash
adb shell "timedatectl timesync-status"   # expect Packet count >= 1, Server: 192.168.100.14
adb shell "date -u '+SciFi-2 UTC: %Y-%m-%d %H:%M:%S UTC'"
```

> **`RootDistanceMaxSec`.** The device drop-in sets `RootDistanceMaxSec=15`.
> Windows' `w32time` NTP server advertises a large root dispersion (~8 s), which
> exceeds `timesyncd`'s default 5 s ceiling; without this, `timesyncd` rejects an
> otherwise-valid reply with *"Server has too large root distance. Disconnecting."*
> and never syncs. Raising the ceiling accepts the host as the reference, which is
> correct on an isolated bench where the host clock is the source by design.

### Manual one-shot push (fallback, no host NTP server) ###

If no host is serving NTP, push the laptop clock to the SciFi-2 directly over adb:

```cmd
python -c "import datetime,subprocess; t=datetime.datetime.now(datetime.timezone.utc); s=t.strftime('%m%d%H%M%Y.%S'); print('Setting SciFi-2 UTC to',t.strftime('%Y-%m-%d %H:%M:%S')); subprocess.run(['adb','shell','date','-u',s],check=True)"
```

Confirm:

```cmd
python -c "import datetime; print('Laptop UTC:',datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))" && adb shell "date -u '+SciFi-2 UTC: %Y-%m-%d %H:%M:%S UTC'"
```

# Temporary `scifi-server` VID Patch #

This repo contains:

```text
scripts/scifi-server-accept-openrb-vid.py
```

The patch adds the OpenRB-150 / ROBOTIS USB VID `0x2F5D` to the existing `scifi-server` USB-device allowlist while preserving both existing accepted VIDs:

```text
0x399A    Science
0x2AC1    SciNetics / RHD adapter
0x2F5D    ROBOTIS OpenRB-150
```

The patch is intended for bench testing. Do **not** replace either existing VID matcher.

The safest deployment method is:

1. Pull the exact running `scifi-server` from the SciFi-2.
2. Patch a local copy.
3. Push the patched binary to `/tmp`.
4. Bind-mount it temporarily over `/opt/scifi/bin/scifi-server`.
5. Restart `scifi-server.service`.
6. Verify the running process is actually using the patched binary.

This leaves the vendor binary on disk untouched. Rebooting the SciFi-2 removes the temporary bind mount and restores the stock executable automatically.

## 3. Pull the exact `scifi-server` binary from SciFi-2 ##

First make sure ADB is connected and rooted:

```cmd
adb root && adb wait-for-device
```

Create the local scratch directory if needed:

```cmd
if not exist .local mkdir .local
```

Pull the exact binary currently installed on the device:

```cmd
adb pull /opt/scifi/bin/scifi-server .local\scifi-server.stock
```

Make the local copy that will be patched:

```cmd
copy /Y .local\scifi-server.stock .local\scifi-server.patched
```

## 4. Verify and patch the local copy ##

Check the patcher's built-in semantic test:

```cmd
python scripts\scifi-server-accept-openrb-vid.py --self-test
```

Verify that the freshly pulled binary matches the exact layout expected by the patcher:

```cmd
python scripts\scifi-server-accept-openrb-vid.py --check .local\scifi-server.patched
```

If `--check` reports the expected original / unpatched state, patch the copy:

```cmd
python scripts\scifi-server-accept-openrb-vid.py .local\scifi-server.patched
```

Verify the patched state:

```cmd
python scripts\scifi-server-accept-openrb-vid.py --check .local\scifi-server.patched
```

Record hashes of both files:

```cmd
certutil -hashfile .local\scifi-server.stock SHA256
certutil -hashfile .local\scifi-server.patched SHA256
```

For the SciFi-2 binary analyzed on 2026-09-11, the observed hashes were:

```text
stock:
2834680e981f8f185855e8a28cc0582651bcdc469885569ba3b3683472640149

patched:
a02a85b31a1f3be214e75ddafc17150a1d8fdeeefdb958b05994111e27800de8
```

These hashes are only reference values for that exact firmware build. Always rely on the patcher's byte guards and `--check` result rather than assuming a future Science update has the same binary.

## 5. Push the patched binary to `/tmp` ##

Push the patched copy:

```cmd
adb push .local\scifi-server.patched /tmp/scifi-server.patched
```

Make it executable and verify it:

```cmd
adb shell "chmod 0755 /tmp/scifi-server.patched; ls -lh /tmp/scifi-server.patched; sha256sum /tmp/scifi-server.patched /opt/scifi/bin/scifi-server"
```

The two hashes should be different at this point because `/opt/scifi/bin/scifi-server` is still the stock binary.

## 6. Verify `/tmp` is executable ##

Check the `/tmp` mount options:

```cmd
adb shell "mount | grep ' /tmp ' || mount | grep tmpfs"
```

The validated SciFi-2 setup reported:

```text
tmpfs on /tmp type tmpfs (rw,nosuid,nodev,...)
```

The important point is that `noexec` is **not** present.

If `/tmp` is ever mounted with `noexec`, do not use it as the source of the bind-mounted executable.

## 7. Confirm how `scifi-server` is supervised ##

Check its PID, cgroup, and systemd unit:

```cmd
adb shell "PID=$(pidof scifi-server); echo PID=$PID; echo === CGROUP ===; cat /proc/$PID/cgroup; echo === SYSTEMD ===; if command -v systemctl >/dev/null 2>&1; then systemctl status $PID --no-pager; else echo no-systemd; fi"
```

On the validated SciFi-2 image this reports:

```text
/system.slice/scifi-server.service
```

and:

```text
scifi-server.service - SciFi Headstage server
```

So the service can be restarted with:

```bash
systemctl restart scifi-server.service
```

## 8. Temporarily bind-mount the patched binary ##

Bind the patched `/tmp` copy over the normal vendor pathname:

```cmd
adb shell "mount --bind /tmp/scifi-server.patched /opt/scifi/bin/scifi-server; echo === BIND ===; mount | grep '/opt/scifi/bin/scifi-server'; echo === HASH ===; sha256sum /opt/scifi/bin/scifi-server /tmp/scifi-server.patched"
```

After this command, both paths should have the same patched SHA-256 hash.

Important: the already-running `scifi-server` process is still executing the old mapped executable until the service is restarted.

## 9. Restart `scifi-server.service` ##

Restart the service and confirm the PID changes:

```cmd
adb shell "OLD=$(pidof scifi-server); systemctl restart scifi-server.service; sleep 2; NEW=$(pidof scifi-server); echo OLD_PID=$OLD NEW_PID=$NEW; systemctl status scifi-server.service --no-pager -l"
```

You want:

```text
Active: active (running)
OLD_PID=<old pid>
NEW_PID=<different pid>
```

## 10. Verify the running process is the patched binary ##

Check the running executable directly through `/proc`:

```cmd
adb shell "PID=$(pidof scifi-server); echo PID=$PID; sha256sum /proc/$PID/exe /tmp/scifi-server.patched; readlink /proc/$PID/exe"
```

The hash of:

```text
/proc/<PID>/exe
```

must exactly match:

```text
/tmp/scifi-server.patched
```

For the validated build this was:

```text
a02a85b31a1f3be214e75ddafc17150a1d8fdeeefdb958b05994111e27800de8
```

## 11. Check startup logs ##

Inspect recent service logs:

```cmd
adb shell "journalctl -u scifi-server.service -n 100 --no-pager -l"
```

Immediately after the validated patched restart, the existing SciNetics/RHD device was rediscovered normally:

```text
Peripheral discovery worker started for device usb-...
Device usb-... discovered IntanRHD2132 peripheral at address 1
Server listening on 0.0.0.0:647
```

Older `Autodiscovery thread caught exception sending broadcast: Network is unreachable` lines may appear in the journal. Check timestamps and PIDs before treating them as failures from the current restart.

## 12. Verify the existing RHD adapter still works ##

The SciNetics/RHD adapter uses:

```text
VID:PID = 2AC1:0004
```

Verify that it is still enumerated:

```cmd
adb shell "for d in /sys/bus/usb/devices/*; do if [ -f \"$d/idVendor\" ]; then V=$(cat \"$d/idVendor\"); P=$(cat \"$d/idProduct\"); if [ \"$V\" = \"2ac1\" ]; then echo $d $V:$P; fi; fi; done"
```

Expected example:

```text
/sys/bus/usb/devices/3-1.2.2 2ac1:0004
```

Verify that the restarted `scifi-server` has a USB device open:

```cmd
adb shell "PID=$(pidof scifi-server); for f in /proc/$PID/fd/*; do t=$(readlink $f 2>/dev/null); case \"$t\" in /dev/bus/usb/*) echo FD=$(basename $f) $t;; esac; done"
```

The exact bus/device number can change after reconnects or restarts.

## 13. Verify the `0xF001` test plugin is loaded ##

The enumeration proof reuses the installed test plugin:

```text
/usr/lib/scifi/plugins/axon_test_source.so
```

Check that the current server loaded it successfully:

```cmd
adb shell "journalctl -u scifi-server.service --no-pager -l | grep -Ei 'plugin|F001|axon.test|loaded.*ID' | tail -50"
```

The validated device reported:

```text
PluginRegistry: loaded /usr/lib/scifi/plugins/axon_test_source.so (axon_test_source v0.1.0, ABI v3, ID 0xf001)
PeripheralRegistry: 1 peripheral plugin(s) loaded from /usr/lib/scifi/plugins
```

At this point the host side is ready for the OpenRB Axon enumeration proof.

## 14. Confirm the OpenRB appears on USB ##

After flashing the `OpenRB_Axon_Enum_Proof` firmware and plugging the OpenRB into the SciFi-2 peripheral USB connection, check for ROBOTIS VID/PID:

```cmd
adb shell "for d in /sys/bus/usb/devices/*; do if [ -f \"$d/idVendor\" ]; then V=$(cat \"$d/idVendor\"); P=$(cat \"$d/idProduct\"); if [ \"$V\" = \"2f5d\" ]; then echo $d $V:$P; fi; fi; done"
```

Expected:

```text
2f5d:2202
```

For live debugging while plugging in the OpenRB:

```cmd
adb shell "journalctl -u scifi-server.service -f -l"
```

The target sequence is approximately:

```text
Connecting device usb-...
USBDevice ... initialized successfully
Peripheral discovery worker started...
... enumerate response ...
... 0xf001 ...
... plugin/factory ...
```

Then from the host machine check the high-level Synapse state:

```cmd
synapsectl -u 192.168.100.157 info
```

The first registration proof is successful once the OpenRB-backed peripheral appears as:

```text
Axon Test Source
```

## 14b. Make the patch survive reboot (persistent bind mount) ##

Sections 5–14 install the patch as a **temporary** `/tmp` bind mount, which a
reboot wipes (`/tmp` is tmpfs; the bind mount is not persisted). To make the
override come back automatically on every boot **without overwriting the vendor
binary**, install a persistent copy plus a systemd unit ordered before
`scifi-server.service`.

The device rootfs is `ext4 rw` and `/opt/scifi/` is writable (verified
2026-09-13), so a file under `/opt/scifi/` persists across reboots. The
mechanism keeps the same non-destructive property as the temporary method: the
vendor binary at `/opt/scifi/bin/scifi-server` is never modified on disk, and
uninstall/revert is just an `umount` plus disabling the unit.

Repo artifacts used here:

```text
scripts/apply-vid-bind-mount.sh          # boot-time apply/revert (runs on device)
scripts/scifi-openrb-vid-patch.service   # systemd unit, Before=scifi-server.service
```

**Safety guard.** The apply step records the stock `scifi-server` SHA-256 and, on
every boot, refuses to bind-mount the patch unless the vendor binary still
matches that hash. If a Science firmware/server update replaces
`scifi-server`, the boot unit logs a clear warning and boots the **stock**
(two-VID) server instead of mounting a stale patch over a new binary. In that
case, re-pull, re-patch (sections 3–4), and re-run the install below.

### Install (operator, over adb) ###

First produce and verify the patched local copy exactly as in sections 3–4, so
`.local\scifi-server.patched` exists and `--check` reports the patched state.
Then, connected and rooted (`adb root && adb wait-for-device`):

Create the persistent patch directory on the device:

```cmd
adb shell "mkdir -p /opt/scifi/patch"
```

Record the current **stock** vendor hash (must be the unpatched binary — confirm
`/proc/<PID>/exe` is the stock hash first, i.e. no bind mount is active):

```cmd
adb shell "sha256sum /opt/scifi/bin/scifi-server > /opt/scifi/patch/scifi-server.stock.sha256; cat /opt/scifi/patch/scifi-server.stock.sha256"
```

Push the persistent patched binary and the two scripts:

```cmd
adb push .local\scifi-server.patched /opt/scifi/patch/scifi-server.patched && adb push scripts\apply-vid-bind-mount.sh /opt/scifi/patch/apply-vid-bind-mount.sh && adb push scripts\scifi-openrb-vid-patch.service /etc/systemd/system/scifi-openrb-vid-patch.service
```

Fix modes. **The patched binary must be executable** — `adb push` creates it
`0666` (no exec bit), and a non-executable file bind-mounted over the vendor
path makes `launch.sh` fail with *"Server binary not found or not executable"*,
crash-looping `scifi-server` and leaving the device stuck on the boot logo. Set
the exec bit on the binary as well as the helper script:

```cmd
adb shell "chmod 0755 /opt/scifi/patch/scifi-server.patched /opt/scifi/patch/apply-vid-bind-mount.sh; chmod 0644 /etc/systemd/system/scifi-openrb-vid-patch.service"
```

Enable and start the unit now (this applies the bind mount immediately and on
every future boot):

```cmd
adb shell "systemctl daemon-reload; systemctl enable --now scifi-openrb-vid-patch.service; systemctl status scifi-openrb-vid-patch.service --no-pager -l"
```

Restart `scifi-server` so it picks up the freshly bind-mounted executable:

```cmd
adb shell "systemctl restart scifi-server.service; sleep 2; PID=$(pidof scifi-server); echo PID=$PID; sha256sum /proc/$PID/exe"
```
The running exe hash must now equal the **patched** hash (`a02a85b3...` for the validated build). Confirm the OpenRB enumerates (section 14) and that
```cmd
synapsectl -u 192.168.100.157 info
``` 
lists the Exo peripheral.

### Verify it survives a reboot ###

```cmd
adb reboot
```

Reconnect after boot
```cmd
adb connect 192.168.100.157:5555
``` 
then confirm the bind mount reasserted itself automatically and the running server is patched:

```cmd
adb shell "echo === UNIT ===; systemctl is-active scifi-openrb-vid-patch.service; echo === BIND ===; grep scifi-server /proc/mounts || echo no-bind-mount; echo === EXE ===; PID=$(pidof scifi-server); sha256sum /proc/$PID/exe"
```

Expected: unit `active`, a bind mount present over
`/opt/scifi/bin/scifi-server`, and `/proc/<PID>/exe` = the patched hash. If the
unit logged a refusal instead, the vendor binary changed — re-pull/re-patch and
re-install.

### Uninstall (restore stock, persistently) ###

```cmd
adb shell "systemctl disable --now scifi-openrb-vid-patch.service; umount /opt/scifi/bin/scifi-server 2>/dev/null; systemctl restart scifi-server.service; sleep 2; PID=$(pidof scifi-server); sha256sum /proc/$PID/exe"
```

The exe hash returns to stock (`2834680e...`). Optionally remove the artifacts:

```cmd
adb shell "rm -rf /opt/scifi/patch; rm -f /etc/systemd/system/scifi-openrb-vid-patch.service; systemctl daemon-reload"
```

## 15. Roll back the temporary patch ##

Because the patched binary is only bind-mounted, rollback does not require copying the stock binary back.

Unmount the temporary override:

```cmd
adb shell "umount /opt/scifi/bin/scifi-server"
```

Restart the service:

```cmd
adb shell "systemctl restart scifi-server.service; sleep 2; systemctl status scifi-server.service --no-pager -l"
```

Verify the stock binary is visible again:

```cmd
adb shell "sha256sum /opt/scifi/bin/scifi-server"
```

For the validated build, the stock hash was:

```text
2834680e981f8f185855e8a28cc0582651bcdc469885569ba3b3683472640149
```

Also verify there is no remaining bind mount:

```cmd
adb shell "grep scifi-server /proc/mounts || echo no-bind-mount"
```

A reboot also clears the temporary `/tmp` file and bind mount, restoring the vendor `scifi-server`.

## 16. Revert a patched local copy ##

If you want to restore a locally patched copy back to the original two-VID logic:

```cmd
python scripts\scifi-server-accept-openrb-vid.py --revert .local\scifi-server.patched
```

Verify:

```cmd
python scripts\scifi-server-accept-openrb-vid.py --check .local\scifi-server.patched
```

This only operates on the local file you specify; it does not modify the SciFi-2 unless you explicitly deploy that file afterward.
