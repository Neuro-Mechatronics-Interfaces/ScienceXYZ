#!/usr/bin/env python3
"""Bench-only patch: make a scifi-server binary accept THREE peripheral VIDs.

scifi-server's usb::USBDeviceManager::discover_devices() accepts a USB device
only if idVendor matches a built-in two-value test:

    mov  w24, #0x399A          ; VID A (Science)          -- unchanged
    mov  w23, #0x2AC1          ; VID B (SciNetics/RHD)    -- unchanged
    ...
    ldrh w1, [sp, #0x88]       ; desc.idVendor
    cmp  w1, w24               ; == 0x399A ?
    ccmp w1, w23, #0x4, ne     ; else == 0x2AC1 ?
    b.ne <skip device>         ; neither -> skip

We must ADD a third accepted VID, 0x2F5D (OpenRB-150 / ROBOTIS), WITHOUT
removing either existing value. 0x399A and 0x2AC1 are register-held and the
three do not share a usable mask/range (a single fixed-bit mask over the three
accepts 2048 VIDs; a range accepts 3802), so a mask is rejected as far too
permissive. Three discrete values need one more compare than the 3-instruction
window can hold, so this uses a minimal, reversible detour into a code cave:

  At the compare site (12 bytes at 0x289708):
      cmp  w1,w24            ->  b   <cave>        ; unconditional detour
      ccmp w1,w23,#4,ne      ->  nop
      b.ne <skip>            ->  nop

  In a zero-filled inter-function code cave (executable .text padding):
      cmp  w1, w24           ; 0x399A
      ccmp w1, w23, #4, ne   ; 0x2AC1     (Z set if either matched)
      b.eq <accept>          ; -> 0x289714 (original fall-through)
      movz w17, #0x2F5D      ; third VID (w17/IP1: ABI scratch, no live value)
      cmp  w1, w17
      b.eq <accept>          ; -> 0x289714
      b    <skip>            ; -> 0x2896e0 (original reject)

Behavior: accept iff idVendor in {0x399A, 0x2AC1, 0x2F5D}; everything else is
rejected exactly as before. Both existing VIDs are preserved bit-for-bit (the
cave re-runs the identical cmp/ccmp for them). w17 (IP1) is the AArch64
intra-procedure-call scratch register and holds no live value across this
straight-line sequence, so clobbering it is safe.

The addresses below were established by reverse-engineering the analyzed
scifi-server (AARCH64 ELF); see docs/axon-usb-enumeration-proof.md. The script
verifies every original byte before writing, so it refuses to patch a binary
that does not match the analyzed layout.

Usage (operator, on a COPY of the device's scifi-server):
    python3 scifi-server-accept-openrb-vid.py --check   scifi-server
    python3 scifi-server-accept-openrb-vid.py           scifi-server   # add 0x2F5D
    python3 scifi-server-accept-openrb-vid.py --revert   scifi-server   # restore
    python3 scifi-server-accept-openrb-vid.py --self-test               # no binary

The agent must NOT run this against a live device. Patch a copy, verify, then
the operator deploys it.
"""

import argparse
import struct
import sys

IMAGE_DELTA = 0x100000  # vaddr - file_offset (confirmed: vaddr 0x2896d0 -> 0x1896d0)

# --- site (vaddrs) --------------------------------------------------------
SITE_CMP = 0x289708   # cmp  w1,w24
SITE_CCMP = 0x28970C  # ccmp w1,w23,#4,ne
SITE_BNE = 0x289710   # b.ne skip
ACCEPT = 0x289714     # original fall-through (accept)
SKIP = 0x2896E0       # original b.ne target (reject)
CAVE = 0x242278       # zero-filled .text code cave (4088 bytes)

NOP = 0xD503201F
COND_EQ = 0
COND_NE = 1


# --- AArch64 encoders -----------------------------------------------------
def enc_b(pc, target):
    return 0x14000000 | (((target - pc) >> 2) & 0x03FFFFFF)


def enc_bcond(pc, target, cond):
    return 0x54000000 | ((((target - pc) >> 2) & 0x7FFFF) << 5) | (cond & 0xF)


def enc_cmp_reg(wn, wm):            # CMP Wn,Wm = SUBS WZR,Wn,Wm (32-bit)
    return 0x6B00001F | (wm << 16) | (wn << 5)


def enc_ccmp_reg(wn, wm, nzcv, cond):  # CCMP Wn,Wm,#nzcv,cond (32-bit)
    return 0x7A400000 | (wm << 16) | (cond << 12) | (wn << 5) | (nzcv & 0xF)


def enc_movz_w(rd, imm16, hw=0):
    return 0x52800000 | (hw << 21) | ((imm16 & 0xFFFF) << 5) | rd


# --- original bytes we expect at the site (guard against wrong binary) ----
ORIG = {
    SITE_CMP: 0x6B18003F,   # cmp  w1,w24
    SITE_CCMP: 0x7A571024,  # ccmp w1,w23,#4,ne
    SITE_BNE: 0x54FFFE81,   # b.ne 0x2896e0
}

# --- the patched site + cave ----------------------------------------------
def build_patch():
    """Return {vaddr: word} for the full patched form."""
    site = {
        SITE_CMP: enc_b(SITE_CMP, CAVE),   # b cave
        SITE_CCMP: NOP,
        SITE_BNE: NOP,
    }
    c = CAVE
    cave = {}
    cave[c] = enc_cmp_reg(1, 24); c += 4               # cmp w1,w24 (0x399A)
    cave[c] = enc_ccmp_reg(1, 23, 4, COND_NE); c += 4  # ccmp w1,w23,#4,ne (0x2AC1)
    cave[c] = enc_bcond(c, ACCEPT, COND_EQ); c += 4     # b.eq accept
    cave[c] = enc_movz_w(17, 0x2F5D); c += 4            # movz w17,#0x2F5D
    cave[c] = enc_cmp_reg(1, 17); c += 4               # cmp w1,w17
    cave[c] = enc_bcond(c, ACCEPT, COND_EQ); c += 4     # b.eq accept
    cave[c] = enc_b(c, SKIP); c += 4                    # b skip
    cave_end = c
    return site, cave, cave_end


# --- static semantic verifier (simulate the cave for the 4 required cases)-
def simulate_cave(vid, cave, cave_end):
    """Execute the cave routine symbolically for a given idVendor.

    Models only the instructions the cave uses (cmp/ccmp/movz/b.eq/b) with a
    tiny NZCV + register machine. Returns 'accept' or 'skip'.
    w24=0x399A, w23=0x2AC1 (as loaded in the function preamble).
    """
    regs = {1: vid & 0xFFFF, 23: 0x2AC1, 24: 0x399A}
    Z = 0
    pc = CAVE
    steps = 0
    while pc < cave_end and steps < 100:
        steps += 1
        w = cave[pc]
        top8 = (w >> 24) & 0xFF
        if (w & 0xFF00001F) == (0x6B00001F & 0xFF00001F) and top8 == 0x6B:  # CMP Wn,Wm
            wn = (w >> 5) & 0x1F
            wm = (w >> 16) & 0x1F
            Z = 1 if (regs.get(wn, 0) & 0xFFFF) == (regs.get(wm, 0) & 0xFFFF) else 0
            pc += 4
        elif top8 == 0x7A:  # CCMP Wn,Wm,#nzcv,cond  (cond here is NE)
            wn = (w >> 5) & 0x1F
            wm = (w >> 16) & 0x1F
            nzcv = w & 0xF
            cond = (w >> 12) & 0xF
            take = (Z == 0) if cond == COND_NE else (Z == 1)
            if take:
                Z = 1 if (regs.get(wn, 0) & 0xFFFF) == (regs.get(wm, 0) & 0xFFFF) else 0
            else:
                Z = (nzcv >> 2) & 1  # #4 -> Z=1; used when the guard is false
            pc += 4
        elif top8 == 0x52:  # MOVZ Wd,#imm16
            rd = w & 0x1F
            imm = (w >> 5) & 0xFFFF
            regs[rd] = imm
            pc += 4
        elif top8 == 0x54:  # B.cond
            cond = w & 0xF
            off = (w >> 5) & 0x7FFFF
            if off & (1 << 18):
                off -= (1 << 19)
            tgt = pc + off * 4
            take = (Z == 1) if cond == COND_EQ else (Z == 0)
            if take:
                return 'accept' if tgt == ACCEPT else ('skip' if tgt == SKIP else 'other:0x%x' % tgt)
            pc += 4
        elif top8 == 0x14:  # B
            off = w & 0x3FFFFFF
            if off & (1 << 25):
                off -= (1 << 26)
            tgt = pc + off * 4
            return 'accept' if tgt == ACCEPT else ('skip' if tgt == SKIP else 'other:0x%x' % tgt)
        else:
            return 'unknown-insn:0x%08x@0x%x' % (w, pc)
    return 'fell-through'


def self_test():
    site, cave, cave_end = build_patch()
    cases = {0x399A: 'accept', 0x2AC1: 'accept', 0x2F5D: 'accept',
             0x1234: 'skip', 0x0000: 'skip', 0xFFFF: 'skip', 0x2AC0: 'skip',
             0x2F5C: 'skip', 0x399B: 'skip'}
    ok = True
    print("Static semantic verification of the patched VID gate:")
    for vid, want in cases.items():
        got = simulate_cave(vid, cave, cave_end)
        status = 'OK' if got == want else 'FAIL'
        if got != want:
            ok = False
        print("  VID 0x%04X -> %-8s (want %-6s) %s" % (vid, got, want, status))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --- file helpers ---------------------------------------------------------
def rd(data, vaddr):
    off = vaddr - IMAGE_DELTA
    return struct.unpack('<I', data[off:off + 4])[0]


def wr(data, vaddr, word):
    off = vaddr - IMAGE_DELTA
    data[off:off + 4] = struct.pack('<I', word)


def cave_is_free(data, cave_end):
    """The cave region must be all-zero in the ORIGINAL binary (unused pad)."""
    for v in range(CAVE, cave_end, 4):
        if rd(data, v) != 0x00000000:
            return False, v
    return True, None


def state_of(data, cave_end):
    """Return 'orig', 'patched', or 'unknown' by inspecting the site."""
    site, cave, _ = build_patch()
    if all(rd(data, v) == ORIG[v] for v in ORIG):
        return 'orig'
    if all(rd(data, v) == site[v] for v in site) and \
       all(rd(data, v) == cave[v] for v in cave):
        return 'patched'
    return 'unknown'


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("binary", nargs='?', help="path to a COPY of scifi-server")
    ap.add_argument("--check", action="store_true", help="report state, do not write")
    ap.add_argument("--revert", action="store_true", help="restore the original two-VID gate")
    ap.add_argument("--self-test", action="store_true",
                    help="statically verify the patch logic; no binary needed")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.binary:
        ap.error("binary path required (or use --self-test)")

    site, cave, cave_end = build_patch()

    with open(args.binary, "rb") as f:
        data = bytearray(f.read())

    st = state_of(data, cave_end)
    print("scifi-server three-VID gate patcher")
    print("  site 0x%08x  cave 0x%08x..0x%08x  current state: %s"
          % (SITE_CMP, CAVE, cave_end, st))

    if st == 'unknown':
        print("ERROR: the compare site does not match the analyzed original OR the "
              "expected patched form. Refusing to touch it.\n"
              "  0x%08x = 0x%08x (orig expects 0x%08x)"
              % (SITE_CMP, rd(data, SITE_CMP), ORIG[SITE_CMP]), file=sys.stderr)
        return 2

    if args.check:
        # Also confirm the cave is available (when original) for a clean apply.
        if st == 'orig':
            free, at = cave_is_free(data, cave_end)
            print("  code cave free (all zero): %s%s"
                  % (free, "" if free else " (first non-zero at 0x%08x)" % at))
        print("  (check only; no write)")
        return 0

    _ = site  # (site/cave dicts drive state_of; kept for symmetry/readability)
    if args.revert:
        if st == 'orig':
            print("  already original; nothing to do.")
            return 0
        for v in ORIG:
            wr(data, v, ORIG[v])
        for v in cave:              # zero the cave back out
            wr(data, v, 0x00000000)
        with open(args.binary, "wb") as f:
            f.write(data)
        print("  REVERTED: restored original two-VID gate and cleared the cave.")
        return 0

    # apply
    if st == 'patched':
        print("  already patched (accepts 0x399A/0x2AC1/0x2F5D); nothing to do.")
        return 0
    free, at = cave_is_free(data, cave_end)
    if not free:
        print("ERROR: code cave 0x%08x is not free (first non-zero at 0x%08x); "
              "refusing." % (CAVE, at), file=sys.stderr)
        return 2
    for v in cave:
        wr(data, v, cave[v])
    for v in site:
        wr(data, v, site[v])
    with open(args.binary, "wb") as f:
        f.write(data)
    print("  PATCHED: now accepts 0x399A, 0x2AC1 and 0x2F5D (added). "
          "Deploy and restart scifi-server.")
    # Re-read and static-verify the just-written form.
    st2 = state_of(data, cave_end)
    print("  post-write state: %s" % st2)
    return 0 if st2 == 'patched' else 3


if __name__ == "__main__":
    raise SystemExit(main())
