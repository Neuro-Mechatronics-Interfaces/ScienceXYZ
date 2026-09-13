#!/bin/sh
# scifi-wlan-powersave.sh -- force wlan0 Wi-Fi power-save OFF on the SciFi-2.
#
# Why: the Qualcomm cnss/wlan radio defaults to power_save "on", which parks the
# radio between packets and only wakes on the AP DTIM/beacon schedule. Measured
# effect on the bench link (host 192.168.100.14 -> SciFi-2 192.168.100.157):
#
#     power_save on : median 11 ms, p99 65 ms, max 125 ms, jitter (stddev) 23 ms
#     power_save off: median  4 ms, p99 ~15 ms, max ~30 ms, jitter (stddev) ~3 ms
#
# ~7x lower jitter -- the figure that matters for synchronization. See
# scripts/measure_link_latency.ps1 for the host-side characterization tool.
#
# This script is invoked in TWO ways so the setting both survives reboot and is
# reasserted after any Wi-Fi drop/reconnect:
#   1. scifi-wlan-powersave-off.service     -- systemd oneshot at boot
#   2. scifi-wlan-powersave-daemon.service  -- wpa_cli action daemon; wpa_cli
#      calls this script with (iface, event) on every association change, so a
#      CONNECT event re-applies power_save off.
#
# Idempotent: calling `iw ... set power_save off` when it is already off is a
# no-op. Safe to run any number of times. Reverse the whole thing by disabling
# both units (see docs/adb.md); the runtime undo is `iw dev wlan0 set power_save on`.
#
# wpa_cli action-script contract: invoked as `<script> <ifname> <event>`.
# We only act on the connect events; everything else is ignored.

IFACE="${1:-wlan0}"
EVENT="${2:-}"

log() { logger -t scifi-wlan-powersave "$*" 2>/dev/null; echo "scifi-wlan-powersave: $*"; }

case "$EVENT" in
    ""|CONNECTED|CTRL-EVENT-CONNECTED)
        # No event (boot oneshot) or a (re)connect: enforce power_save off.
        if iw dev "$IFACE" set power_save off 2>/dev/null; then
            state=$(iw dev "$IFACE" get power_save 2>/dev/null)
            log "$IFACE power_save -> off (event='${EVENT:-boot}'): $state"
        else
            log "WARNING: failed to set power_save off on $IFACE (event='${EVENT:-boot}')"
            exit 1
        fi
        ;;
    *)
        # Disconnect / scan / other events: nothing to do.
        :
        ;;
esac
exit 0
