#!/bin/sh
# apply-vid-bind-mount.sh -- re-establish the OpenRB three-VID scifi-server
# patch as a bind mount at boot, without ever overwriting the vendor binary.
#
# This is the persistent replacement for the manual /tmp bind-mount procedure
# in docs/adb.md. It is driven by scifi-openrb-vid-patch.service (systemd,
# ordered Before=scifi-server.service). It lives ON THE DEVICE at
#   /opt/scifi/patch/apply-vid-bind-mount.sh
# alongside the persistent patched binary and the recorded stock hash.
#
# SAFETY MODEL
#   VENDOR    = /opt/scifi/bin/scifi-server         (untouched vendor binary)
#   PATCHED   = /opt/scifi/patch/scifi-server.patched (our persistent copy)
#   STOCKHASH = /opt/scifi/patch/scifi-server.stock.sha256 (expected VENDOR hash)
#
#   On "apply" the script refuses to bind-mount unless VENDOR still matches the
#   recorded stock hash. If a Science firmware/server update replaced VENDOR,
#   the hash will differ and we log a clear warning and do NOTHING -- because a
#   patch carved for the old binary layout must never be mounted over a new one.
#   In that case the operator re-pulls, re-patches, and re-installs (see
#   docs/adb.md). This turns "silent revert after an update" into a visible,
#   logged refusal.
#
#   The vendor binary on disk is never modified; revert is just `umount`, and a
#   reboot with the unit disabled restores stock behavior automatically.

set -eu

VENDOR="/opt/scifi/bin/scifi-server"
PATCHDIR="/opt/scifi/patch"
PATCHED="$PATCHDIR/scifi-server.patched"
STOCKHASH_FILE="$PATCHDIR/scifi-server.stock.sha256"

log() { echo "scifi-openrb-vid-patch: $*"; }

sha256_of() {
    # Print just the hex digest of "$1", or empty string if unreadable.
    if [ -r "$1" ]; then
        sha256sum "$1" 2>/dev/null | awk '{print $1}'
    else
        echo ""
    fi
}

is_bind_active() {
    # True if VENDOR currently has a bind mount over it.
    grep -q " $VENDOR " /proc/mounts 2>/dev/null
}

cmd_apply() {
    if [ ! -f "$PATCHED" ]; then
        log "ERROR: patched binary $PATCHED missing; nothing to mount. Re-install."
        exit 1
    fi
    if [ ! -x "$PATCHED" ]; then
        # A non-executable file bind-mounted over the vendor path makes
        # launch.sh reject it ("Server binary not found or not executable") and
        # crash-loops scifi-server, stranding the device on the boot logo. Refuse
        # and boot stock instead (exit 0 = safe fallback, warning in the journal).
        log "REFUSING to bind-mount: $PATCHED is not executable (mode missing +x)."
        log "  Run: chmod 0755 $PATCHED  then: systemctl restart scifi-server.service"
        log "  Booting the STOCK (two-VID) server; the OpenRB will not enumerate."
        exit 0
    fi
    if [ ! -f "$STOCKHASH_FILE" ]; then
        log "ERROR: recorded stock hash $STOCKHASH_FILE missing; refusing (cannot"
        log "       confirm the vendor binary is the one this patch was built for)."
        exit 1
    fi

    want_stock="$(cat "$STOCKHASH_FILE" | awk '{print $1}')"
    have_vendor="$(sha256_of "$VENDOR")"

    if [ -z "$have_vendor" ]; then
        log "ERROR: cannot read vendor binary $VENDOR; refusing."
        exit 1
    fi

    if [ "$have_vendor" != "$want_stock" ]; then
        log "REFUSING to bind-mount: vendor binary changed."
        log "  $VENDOR now $have_vendor"
        log "  expected stock  $want_stock"
        log "  A firmware/server update likely replaced scifi-server. Re-pull,"
        log "  re-patch, and re-install this override (see docs/adb.md). Booting"
        log "  with the STOCK (two-VID) server; the OpenRB will not enumerate."
        # Exit 0 so scifi-server still starts on stock; this is a safe fallback,
        # not a boot failure. The warning is in the journal for the operator.
        exit 0
    fi

    if is_bind_active; then
        log "bind mount already active over $VENDOR; nothing to do."
        exit 0
    fi

    mount --bind "$PATCHED" "$VENDOR"
    log "bind-mounted patched binary over $VENDOR (three-VID: 399A/2AC1/2F5D)."
}

cmd_revert() {
    if is_bind_active; then
        umount "$VENDOR"
        log "unmounted patch; $VENDOR restored to vendor binary."
    else
        log "no bind mount present; nothing to revert."
    fi
}

case "${1:-}" in
    apply)  cmd_apply ;;
    revert) cmd_revert ;;
    *)
        echo "usage: $0 {apply|revert}" >&2
        exit 2
        ;;
esac
