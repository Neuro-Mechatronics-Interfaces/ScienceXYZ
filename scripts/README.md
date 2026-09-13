# scripts/ — reproducible bring-up, bench, and diagnostic utilities

Operator- and developer-run helpers for the SciFi-2 bench. These are tooling for a human at the console; none of them are part of a deployed Synapse App, and per the AGENTS.md CLI-execution boundary the agent never runs the device-facing ones. PowerShell scripts (`.ps1`) run on the Windows host; `.sh` scripts run on the SciFi-2 device; Python scripts run wherever noted below.

Most `.ps1` scripts that touch Windows Time or the firewall require an **elevated (Administrator) PowerShell** and check for it on start. They are written to be idempotent and to include a `-Disable` / `-Uninstall` / `-Restore` inverse where they change durable system state.

## Time synchronization

The SciFi-2 has no working battery-backed RTC and the bench LAN (`192.168.100.0/24`) has no internet route and no router-provided NTP, so a host on that segment must be the time source. Three scripts cover the possible roles.

### `sync_time_from_macbook.ps1` — pull this Windows laptop's clock from the Mac

Use when the M4 MacBook Pro (`192.168.100.106`) is the trusted clock and this Windows 11 laptop should match it. Run elevated:

```powershell
scripts\sync_time_from_macbook.ps1
```

What it does:

1. Pings `192.168.100.106`; exits without changing anything if the Mac is absent.
2. Sends a real SNTP request to `192.168.100.106:123` to confirm the Mac is
   *answering* NTP (not merely up).
3. Points `w32tm` at the Mac, resyncs once, and prints source/status plus a
   `w32tm /stripchart` offset check.
4. By default restores the previous Windows time source afterward (a one-shot
   sync). Pass `-Persist` to keep the Mac as the ongoing source for a long bench
   session; undo that later with `-Restore`.

`-Persist` does **not** pin the Mac as the only source. It writes a fallback peer
list with the Mac first and the internet pool after it, so `w32tm` uses the Mac
while it is reachable and automatically falls back to internet time
(`time.windows.com` / `pool.ntp.org` / `time.google.com`) if the Mac leaves the
network. Override that list with `-InternetFallback "<peers>"`.

**Caveat:** stock macOS does **not** serve NTP to LAN peers — it runs a time *client*, not a server. If step 2 finds the Mac up but silent on UDP 123, the script changes nothing on Windows and prints the exact `sudo` commands to enable NTP serving on the Mac (and a reminder to allow inbound UDP 123 in the Mac's firewall). Expect the first run to tell you to enable serving on the Mac once; subsequent runs then sync. Override the target with `-MacIp <addr>`.

Parameters: `-MacIp` (default `192.168.100.106`), `-InternetFallback`,
`-Persist`, `-Restore`.

### `setup_laptop_ntp_server.ps1` — make this laptop the bench NTP server (w32time)

The inverse role: this Windows laptop disciplines itself from the public NTP pool (via its internet path) **and** serves NTP on `192.168.100.14:123` so the SciFi-2 syncs from it. Uses the built-in Windows Time service, so it is durable across reboots, and opens the inbound firewall. Run elevated; revert with `-Disable`.

Prefer this when you want the served time disciplined from the internet automatically.

### `install_bench_ntp_task.ps1` + `bench_ntp_server.py` — serve NTP from the Python responder

`bench_ntp_server.py` is a minimal SNTPv4 responder that serves the host's *current* clock on UDP 123 (works on Windows/Linux/macOS; binding 123 needs privilege). `install_bench_ntp_task.ps1` registers it as a Windows Scheduled Task that starts at boot as SYSTEM and restarts on failure. Run the installer elevated; undo with `-Uninstall`.

Prefer this over the w32time server role only when you specifically want `bench_ntp_server.py` as the server; keep Windows time sync on so the served clock stays accurate.

## Wi-Fi link latency / power-save (device sync)

The SciFi-2 Qualcomm `wlan0` radio defaults to `power_save on`, which adds ~23 ms
of RTT jitter over the bench Wi-Fi link — unusable for synchronization. Turning it
off drops jitter ~7x (to ~3 ms). Full rationale, measured numbers, and the
persistent install/verify/uninstall procedure are in `docs/adb.md` §17.

- **`measure_link_latency.ps1`** (host) — characterizes host→device latency as a
  full distribution (min/mean/median/p95/p99/max, stddev jitter, loss) instead of
  `ping`'s 4-sample average. Use it for before/after comparisons; jitter is the
  figure of merit. Host-side only; never touches the device.
- **`scifi-wlan-powersave.sh`** (device) — sets `iw dev wlan0 set power_save off`.
  Idempotent; runs both at boot and as a `wpa_cli` action script on reconnect.
- **`scifi-wlan-powersave-off.service`** (device) — systemd oneshot that applies
  it at boot, ordered after `scifi-wifi-connect.service`.
- **`scifi-wlan-powersave-daemon.service`** (device) — a `wpa_cli -a` action
  daemon on the vendor supplicant's control socket that reasserts the setting on
  every (re)association, without modifying the vendor `wpa_supplicant` invocation.

## Host recorder build

### `build-recorder-windows.ps1`

Builds the native Windows `task-recorder.exe` (MSVC + vcpkg: grpc/protobuf, cppzmq/zeromq, hdf5) so `calibrate-gui` and the Reactions bridge — Windows processes — can spawn it. The Linux ELF under `build/raw-recorder` cannot be launched by a Windows process (WinError 193); this produces a PE binary instead. Point the GUI's "Recorder path" at `build-win/raw-recorder/task-recorder.exe`. See the header for the vcpkg prerequisites.

## OpenRB three-VID scifi-server patch (device)

Lets the OpenRB-150 Exo (VID `0x2F5D`) enumerate through `scifi-server`, whose stock USB gate accepts only `0x399A` and `0x2AC1`. See AGENTS.md ("OpenRB Three-VID scifi-server Patch") and `docs/adb.md` §14/§14b for the full procedure.

- **`scifi-server-accept-openrb-vid.py`** — binary-patches a **copy** of the
  vendor `scifi-server` to add the third VID without removing either existing one
  (minimal reversible detour into a code cave). The agent must never run this
  against a live device; patch a copy, the operator deploys.
- **`apply-vid-bind-mount.sh`** — device-side script (installed at
  `/opt/scifi/patch/`) that re-establishes the patch as a **bind mount** over the
  vendor path each boot, refusing to mount unless the vendor binary still matches
  a recorded stock SHA-256. Never overwrites the vendor binary.
- **`scifi-openrb-vid-patch.service`** — the systemd unit
  (`Before=scifi-server.service`) that drives the bind mount at boot.

## Diagnostics and verification

- **`device-diag/`** — small Debian packages whose `postinst` gathers on-device
  state, delivered over `synapsectl peripherals deploy --package` (no SSH). See
  [`device-diag/README.md`](device-diag/README.md) for the mailbox pattern and
  package list.
- **`check_motion_lut.py`** — developer tool that verifies the tracked
  `config/motion_lut.json` against the external Reaction-Task JS source
  (`motion_catalog.js`) and reports drift. Tolerant text parse, no JS engine;
  nonzero exit on divergence or a missing source. The host never reads the JS
  repo at runtime.
- **`alignment_acceptance/analyze_alignment.py`** — validates measured
  cross-source alignment captures (JSON trial records) and emits a
  machine-readable report of missing bounds, loss, and provenance. It does not
  estimate a clock or repair a stream. `test_analyze_alignment.py` is its
  hardware-free unit test. See `docs/alignment-acceptance.md`.

## Conventions for new scripts

- Put reproducible bring-up/build/test utilities here; keep raw recordings out of
  Git (they live under `data/`).
- Windows system-state scripts: check for Administrator, be idempotent, and
  provide an inverse (`-Disable`/`-Uninstall`/`-Restore`).
- Never run `synapsectl` or command the device from a script intended for the
  agent or a headless/CI path; device-facing patches operate on a copy and leave
  deployment to the operator (see AGENTS.md).
