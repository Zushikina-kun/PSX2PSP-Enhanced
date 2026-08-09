"""Smoke test for patches.py — IPS/BPS/xdelta format support."""
import sys, os, struct, tempfile, zlib
sys.path.insert(0, os.path.dirname(__file__))

from modules.patches import (
    apply_ips, apply_bps, patch_iso, detect_patch_format,
    apply_patches_to_iso, PatchCandidate, xdelta3_available,
)

PASS, FAIL = "PASS", "FAIL"
results = []

def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))

# ── detect_patch_format ───────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    ips_f = os.path.join(tmp, "p.ips")
    bps_f = os.path.join(tmp, "p.bps")
    with open(ips_f, "wb") as f: f.write(b"PATCH" + b"EOF")
    with open(bps_f, "wb") as f: f.write(b"BPS1" + bytes(20))
    check("detect .ips by magic", detect_patch_format(ips_f) == "ips")
    check("detect .bps by magic", detect_patch_format(bps_f) == "bps")

# ── apply_ips: identity patch (no records) ────────────────────────────────────
src     = b"Hello World! " * 200
empty   = b"PATCH" + b"EOF"
result  = apply_ips(src, empty)
check("IPS identity (empty patch)", result == src, f"{len(result)} bytes")

# ── apply_ips: normal record ──────────────────────────────────────────────────
patch = (
    b"PATCH"
    + b"\x00\x00\x00"      # offset 0 (3 bytes big-endian)
    + b"\x00\x05"          # size 5
    + b"PATCD"             # replacement data
    + b"EOF"
)
patched = apply_ips(src, patch)
check("IPS normal record: first 5 bytes replaced", patched[:5] == b"PATCD",
      repr(patched[:5]))
check("IPS normal record: rest unchanged",         patched[5:] == src[5:])

# ── apply_ips: RLE record ─────────────────────────────────────────────────────
rle_patch = (
    b"PATCH"
    + b"\x00\x00\x00"      # offset 0
    + b"\x00\x00"          # size = 0 → RLE record
    + b"\x00\x08"          # count = 8
    + b"\xFF"              # fill byte
    + b"EOF"
)
rle_result = apply_ips(src, rle_patch)
check("IPS RLE record: 8 bytes filled with 0xFF", rle_result[:8] == b"\xFF" * 8,
      repr(rle_result[:8]))
check("IPS RLE record: rest unchanged",            rle_result[8:] == src[8:])

# ── apply_ips: mid-file record ────────────────────────────────────────────────
mid_patch = (
    b"PATCH"
    + b"\x00\x00\x0A"      # offset 10
    + b"\x00\x03"          # size 3
    + b"XYZ"
    + b"EOF"
)
mid = apply_ips(src, mid_patch)
check("IPS mid-file record", mid[10:13] == b"XYZ", repr(mid[10:13]))
check("IPS mid: before offset unchanged", mid[:10] == src[:10])
check("IPS mid: after record unchanged",  mid[13:] == src[13:])

# ── patch_iso dispatch ────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    iso_src = os.path.join(tmp, "game.bin")
    out     = os.path.join(tmp, "game_p.bin")
    ipt     = os.path.join(tmp, "patch.ips")
    with open(iso_src, "wb") as f:
        f.write(b"ABCDE" + b"\x00" * 200)
    with open(ipt, "wb") as f:
        f.write(b"PATCH")
        f.write(b"\x00\x00\x00")   # offset 0
        f.write(b"\x00\x05")        # length 5
        f.write(b"PATCD")
        f.write(b"EOF")
    ok = patch_iso(iso_src, ipt, out)
    check("patch_iso IPS: returns True", ok)
    check("patch_iso IPS: output file created", os.path.isfile(out))
    if os.path.isfile(out):
        with open(out, "rb") as f: d = f.read()
        check("patch_iso IPS: output starts with PATCD", d[:5] == b"PATCD",
              repr(d[:5]))
        check("patch_iso IPS: rest unchanged", d[5:] == b"\x00" * 200)

# ── patch_iso ZIP dispatch ────────────────────────────────────────────────────
import zipfile
with tempfile.TemporaryDirectory() as tmp:
    iso_src = os.path.join(tmp, "game.bin")
    out     = os.path.join(tmp, "game_zp.bin")
    ipt_ips = os.path.join(tmp, "patch.ips")
    zp      = os.path.join(tmp, "patch.zip")
    with open(iso_src, "wb") as f:
        f.write(b"HELLO" + b"\x00" * 100)
    with open(ipt_ips, "wb") as f:
        f.write(b"PATCH")
        f.write(b"\x00\x00\x00")
        f.write(b"\x00\x05")
        f.write(b"WORLD")
        f.write(b"EOF")
    with zipfile.ZipFile(zp, "w") as z:
        z.write(ipt_ips, "patch.ips")
    ok = patch_iso(iso_src, zp, out)
    check("patch_iso ZIP: returns True", ok)
    if os.path.isfile(out):
        with open(out, "rb") as f: d = f.read()
        check("patch_iso ZIP: patch applied from zip", d[:5] == b"WORLD",
              repr(d[:5]))

# ── apply_patches_to_iso no-op ───────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    iso = os.path.join(tmp, "g.bin")
    with open(iso, "wb") as f: f.write(b"DATA" * 10)
    r = apply_patches_to_iso(iso, [], tmp)
    check("apply_patches_to_iso empty = passthrough", r == iso)

# ── apply_patches_to_iso with local candidate ─────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    iso = os.path.join(tmp, "g.bin")
    ipt = os.path.join(tmp, "p.ips")
    out_iso = os.path.join(tmp, "patched_1_g.bin")
    with open(iso, "wb") as f: f.write(b"AAAA" + b"\x00" * 50)
    with open(ipt, "wb") as f:
        f.write(b"PATCH")
        f.write(b"\x00\x00\x00")
        f.write(b"\x00\x04")
        f.write(b"BBBB")
        f.write(b"EOF")
    cand = PatchCandidate(source="local", title="test", url="",
                          patch_type="ips", local_path=ipt)
    result = apply_patches_to_iso(iso, [cand], tmp)
    check("apply_patches_to_iso with candidate: path changed", result != iso,
          os.path.basename(result))
    if result != iso and os.path.isfile(result):
        with open(result, "rb") as f: d = f.read()
        check("apply_patches_to_iso: patch applied", d[:4] == b"BBBB",
              repr(d[:4]))

# ── xdelta3 availability (optional — just report don't fail) ──────────────────
avail = xdelta3_available()
print(f"  [INFO] xdelta3 available: {avail}")

# ── Summary ───────────────────────────────────────────────────────────────────
n, p = len(results), sum(results)
print()
print(f"=== {p}/{n} passed" + (" ALL PASS" if p == n else f" {n-p} FAILED") + " ===")
sys.exit(0 if p == n else 1)
