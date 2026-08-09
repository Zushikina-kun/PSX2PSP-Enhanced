"""
patches.py — PSX patch search, download, and application pipeline.

Supported patch formats:
  IPS  — International Patching System (simple offset+data records)
  BPS  — Beat Patch System (delta encoding, CRC32 verified)
  xdelta3 — requires xdelta3.exe/xdelta3 binary in PATH or tools/

Auto-search sources (tried in order):
  1. romhacking.net — platform 6 (PlayStation), search by game title
  2. Archive.org    — search metadata for patch files
  3. PSX-Place      — tag pages for ps1-patches / english-patch

All downloaded patches are cached in CACHE_DIR/patches/<serial>/
A PatchCandidate dataclass holds every result.
patch_iso(iso_path, patch_path, out_path) applies any supported format.
"""

import os
import re
import json
import shutil
import struct
import subprocess
import urllib.parse
import urllib.request
import hashlib
import zipfile
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Callable, List, Tuple

from .constants import CACHE_DIR, ROOT_DIR
from .bgm import clean_title, _http_get, _http_text, _HEADERS

# ── PatchCandidate dataclass ──────────────────────────────────────────────────

@dataclass
class PatchCandidate:
    """One downloadable patch the user can choose from."""
    source:       str          # "romhacking" | "archive" | "psxplace" | "local"
    title:        str          # patch title / description
    url:          str          # direct download URL
    patch_type:   str  = ""    # "ips" | "bps" | "xdelta" | "zip" | "unknown"
    game_title:   str  = ""
    version:      str  = ""
    author:       str  = ""
    description:  str  = ""
    local_path:   str  = ""    # set after download

    def label(self) -> str:
        parts = [self.title]
        if self.author:
            parts.append(f"by {self.author}")
        if self.version:
            parts.append(f"v{self.version}")
        parts.append(f"[{self.source}]")
        return "  ".join(parts)

    @property
    def ext(self) -> str:
        if self.patch_type:
            return self.patch_type
        return os.path.splitext(self.url)[1].lstrip(".").lower()


# ── Patch cache helpers ───────────────────────────────────────────────────────

def _patch_cache_dir(serial: str) -> str:
    d = os.path.join(CACHE_DIR, "patches", serial)
    os.makedirs(d, exist_ok=True)
    return d


def _cached_patch(serial: str, filename: str) -> str:
    return os.path.join(_patch_cache_dir(serial), filename)


# ═══════════════════════════════════════════════════════════════════════════════
# IPS PARSER / APPLIER
# ═══════════════════════════════════════════════════════════════════════════════

_IPS_MAGIC  = b"PATCH"
_IPS_EOF    = b"EOF"
_IPS_TRUNC  = b"EOF"   # same bytes — file may end with optional truncation record


def apply_ips(source: bytes, patch: bytes) -> bytes:
    """
    Apply an IPS patch to *source* bytes and return the patched data.
    Raises ValueError on malformed patch.
    """
    if not patch.startswith(_IPS_MAGIC):
        raise ValueError("Not a valid IPS file (missing PATCH header)")
    data = bytearray(source)
    pos  = 5  # skip "PATCH"

    while pos < len(patch):
        # 3-byte offset
        if pos + 3 > len(patch):
            break
        offset_bytes = patch[pos:pos + 3]
        if offset_bytes == _IPS_EOF:
            # Optional truncation size follows (2 bytes big-endian)
            if pos + 5 <= len(patch):
                trunc = struct.unpack(">H", patch[pos + 3:pos + 5])[0]
                del data[trunc:]
            break
        offset = struct.unpack(">I", b"\x00" + offset_bytes)[0]
        pos += 3

        # 2-byte length
        size = struct.unpack(">H", patch[pos:pos + 2])[0]
        pos += 2

        if size == 0:
            # RLE record: 2-byte count + 1-byte fill value
            count = struct.unpack(">H", patch[pos:pos + 2])[0]
            fill  = patch[pos + 2]
            pos  += 3
            end   = offset + count
            if end > len(data):
                data.extend(b"\x00" * (end - len(data)))
            data[offset:end] = bytes([fill]) * count
        else:
            # Normal record
            payload = patch[pos:pos + size]
            pos    += size
            end     = offset + size
            if end > len(data):
                data.extend(b"\x00" * (end - len(data)))
            data[offset:end] = payload

    return bytes(data)


# ═══════════════════════════════════════════════════════════════════════════════
# BPS PARSER / APPLIER
# ═══════════════════════════════════════════════════════════════════════════════

def _bps_decode_number(data: bytes, pos: int) -> Tuple[int, int]:
    """Decode a BPS variable-length encoded integer. Returns (value, new_pos)."""
    value, shift = 0, 1
    while True:
        x      = data[pos]; pos += 1
        value += (x & 0x7F) * shift
        if x & 0x80:
            break
        shift <<= 7
        value += shift
    return value, pos


def apply_bps(source: bytes, patch: bytes) -> bytes:
    """
    Apply a BPS (Beat Patch System) patch.
    Verifies source and patch CRC32 before applying.
    Raises ValueError on checksum mismatch or malformed patch.
    """
    import zlib
    if not patch.startswith(b"BPS1"):
        raise ValueError("Not a valid BPS file (missing BPS1 header)")

    # Footer: last 12 bytes = source_crc32 + target_crc32 + patch_crc32 (each 4 bytes LE)
    if len(patch) < 16:
        raise ValueError("BPS patch too short")

    patch_crc_stored   = struct.unpack_from("<I", patch, len(patch) - 4)[0]
    target_crc_stored  = struct.unpack_from("<I", patch, len(patch) - 8)[0]
    source_crc_stored  = struct.unpack_from("<I", patch, len(patch) - 12)[0]

    # Verify patch file itself
    actual_patch_crc = zlib.crc32(patch[:-4]) & 0xFFFFFFFF
    if actual_patch_crc != patch_crc_stored:
        raise ValueError(f"BPS patch CRC32 mismatch: {actual_patch_crc:#010x} != {patch_crc_stored:#010x}")

    # Verify source
    actual_source_crc = zlib.crc32(source) & 0xFFFFFFFF
    if actual_source_crc != source_crc_stored:
        raise ValueError(
            f"BPS source CRC32 mismatch: {actual_source_crc:#010x} != {source_crc_stored:#010x}\n"
            "The patch was made for a different version of this ROM.")

    pos = 4  # skip "BPS1"
    source_size,  pos = _bps_decode_number(patch, pos)
    target_size,  pos = _bps_decode_number(patch, pos)
    metadata_len, pos = _bps_decode_number(patch, pos)
    pos += metadata_len  # skip metadata

    target  = bytearray(target_size)
    out_pos = 0
    src_pos = 0
    cpy_pos = 0

    while pos < len(patch) - 12:
        data_val, pos = _bps_decode_number(patch, pos)
        cmd    = data_val & 3
        length = (data_val >> 2) + 1

        if cmd == 0:   # SourceRead
            target[out_pos:out_pos + length] = source[out_pos:out_pos + length]
            out_pos += length

        elif cmd == 1:  # TargetRead
            target[out_pos:out_pos + length] = patch[pos:pos + length]
            pos     += length
            out_pos += length

        elif cmd == 2:  # SourceCopy
            offset_data, pos = _bps_decode_number(patch, pos)
            src_pos += (-(offset_data & 1) if offset_data & 1 else 1) * (offset_data >> 1)
            while length > 0:
                target[out_pos] = source[src_pos]
                src_pos += 1; out_pos += 1; length -= 1

        else:           # TargetCopy
            offset_data, pos = _bps_decode_number(patch, pos)
            cpy_pos += (-(offset_data & 1) if offset_data & 1 else 1) * (offset_data >> 1)
            while length > 0:
                target[out_pos] = target[cpy_pos]
                cpy_pos += 1; out_pos += 1; length -= 1

    result = bytes(target)
    actual_target_crc = zlib.crc32(result) & 0xFFFFFFFF
    if actual_target_crc != target_crc_stored:
        raise ValueError(
            f"BPS output CRC32 mismatch: {actual_target_crc:#010x} != {target_crc_stored:#010x}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# XDELTA3 APPLIER
# ═══════════════════════════════════════════════════════════════════════════════

def _find_xdelta3() -> str:
    """Return path to xdelta3 binary or empty string."""
    # Check bundled location first
    bundled = os.path.join(str(ROOT_DIR), "tools", "xdelta3.exe")
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("xdelta3") or shutil.which("xdelta3.exe")
    return found or ""


def apply_xdelta(source_path: str, patch_path: str, out_path: str) -> bool:
    """
    Apply an xdelta3 patch to *source_path*, writing result to *out_path*.
    Returns True on success. Requires xdelta3 binary.
    """
    xd3 = _find_xdelta3()
    if not xd3:
        raise RuntimeError(
            "xdelta3 not found. Download it from https://github.com/jmacd/xdelta-gpl/releases "
            "and place xdelta3.exe in the project root tools/ folder.")
    try:
        result = subprocess.run(
            [xd3, "-d", "-s", source_path, patch_path, out_path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300
        )
        return result.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED patch_iso() ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def detect_patch_format(patch_path: str) -> str:
    """
    Detect the format of a patch file by magic bytes and extension.
    Returns "ips" | "bps" | "xdelta" | "zip" | "unknown".
    """
    ext = os.path.splitext(patch_path)[1].lower().lstrip(".")
    if ext in ("ips",):
        return "ips"
    if ext in ("bps",):
        return "bps"
    if ext in ("xdelta", "xdelta3", "vcdiff"):
        return "xdelta"
    if ext in ("zip", "7z", "rar"):
        return "zip"
    # Fall back to magic bytes
    try:
        with open(patch_path, "rb") as f:
            magic = f.read(5)
        if magic == b"PATCH":
            return "ips"
        if magic[:4] == b"BPS1":
            return "bps"
        if magic[:9] == b"\xd6\xc3\xc4":  # xdelta3 magic
            return "xdelta"
    except Exception:
        pass
    return "unknown"


def patch_iso(
    iso_path:   str,
    patch_path: str,
    out_path:   str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Apply *patch_path* to *iso_path*, writing result to *out_path*.
    Handles IPS, BPS, xdelta3, and ZIP archives containing a patch.
    Returns True on success.
    """
    def _log(m): progress_cb and progress_cb(m)

    fmt = detect_patch_format(patch_path)
    _log(f"Applying {fmt.upper()} patch: {os.path.basename(patch_path)}…")

    # ── ZIP: extract first matching patch file and recurse ────────────────────
    if fmt == "zip" or patch_path.lower().endswith(".zip"):
        with tempfile.TemporaryDirectory(prefix="psx2psp_patch_") as tmp:
            with zipfile.ZipFile(patch_path) as zf:
                names  = zf.namelist()
                # Prefer IPS/BPS/xdelta inside the zip
                wanted = [n for n in names
                          if any(n.lower().endswith(e)
                                 for e in (".ips", ".bps", ".xdelta", ".xdelta3"))]
                if not wanted:
                    _log("ZIP contains no supported patch file (.ips/.bps/.xdelta).")
                    return False
                extracted = os.path.join(tmp, os.path.basename(wanted[0]))
                with open(extracted, "wb") as f:
                    f.write(zf.read(wanted[0]))
            return patch_iso(iso_path, extracted, out_path, progress_cb)

    # ── IPS ───────────────────────────────────────────────────────────────────
    if fmt == "ips":
        try:
            with open(iso_path, "rb") as f:
                source = f.read()
            with open(patch_path, "rb") as f:
                patch_data = f.read()
            result = apply_ips(source, patch_data)
            with open(out_path, "wb") as f:
                f.write(result)
            _log(f"IPS patch applied → {os.path.getsize(out_path):,} bytes")
            return True
        except Exception as e:
            _log(f"IPS apply failed: {e}")
            return False

    # ── BPS ───────────────────────────────────────────────────────────────────
    if fmt == "bps":
        try:
            with open(iso_path, "rb") as f:
                source = f.read()
            with open(patch_path, "rb") as f:
                patch_data = f.read()
            result = apply_bps(source, patch_data)
            with open(out_path, "wb") as f:
                f.write(result)
            _log(f"BPS patch applied → {os.path.getsize(out_path):,} bytes")
            return True
        except Exception as e:
            _log(f"BPS apply failed: {e}")
            return False

    # ── xdelta3 ───────────────────────────────────────────────────────────────
    if fmt == "xdelta":
        try:
            ok = apply_xdelta(iso_path, patch_path, out_path)
            if ok:
                _log(f"xdelta3 patch applied → {os.path.getsize(out_path):,} bytes")
            else:
                _log("xdelta3 patch failed (check xdelta3 binary).")
            return ok
        except Exception as e:
            _log(f"xdelta3 error: {e}")
            return False

    _log(f"Unsupported patch format: {fmt}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 1 — romhacking.net (platform 6 = PlayStation)
# ═══════════════════════════════════════════════════════════════════════════════
# romhacking.net has no public API — we scrape their translation/hack search.
# URL: https://www.romhacking.net/translations/?page=1&platform=6&perpage=20
#      &title=GAME+NAME&status=2 (status 2 = fully playable)
# Each result card contains the patch title, author, patch type link.

_RHN_BASE   = "https://www.romhacking.net"
_RHN_SEARCH = ("https://www.romhacking.net/translations/"
                "?page=1&platform=6&perpage=20&title={query}&status=")
_RHN_HACKS  = ("https://www.romhacking.net/hacks/"
               "?page=1&platform=6&perpage=20&title={query}")


def _rhn_parse_results(html: str, source_label: str) -> List[PatchCandidate]:
    """Extract patch entries from a romhacking.net search results page."""
    candidates: List[PatchCandidate] = []

    # Each entry is a table row; patch detail page link looks like:
    #   href="/translations/1234/"  or  href="/hacks/567/"
    detail_links = re.findall(
        r'href="((?:/translations/|/hacks/)\d+/)"', html)
    # Parallel: extract titles from nearby <td class="col_1"> or <b> tags
    titles = re.findall(
        r'class="col_1"[^>]*>.*?<a[^>]+>([^<]+)</a>', html, re.DOTALL)
    authors = re.findall(
        r'class="col_3"[^>]*>\s*([^<\n]+)', html)

    seen = set()
    for i, link in enumerate(detail_links):
        if link in seen:
            continue
        seen.add(link)
        title  = titles[i].strip()  if i < len(titles)  else link
        author = authors[i].strip() if i < len(authors) else ""

        candidates.append(PatchCandidate(
            source     = source_label,
            title      = title,
            url        = _RHN_BASE + link,   # detail page — resolved to patch file later
            patch_type = "ips",              # most RHN patches are IPS
            author     = author,
        ))
    return candidates


def _rhn_resolve_download(detail_url: str) -> str:
    """
    Visit a romhacking.net patch detail page and return the direct download URL.
    Patch download links look like:  /utils/download/1234/
    """
    html = _http_text(detail_url, timeout=12)
    if not html:
        return ""
    m = re.search(r'href="(/utils/download/\d+/)"', html)
    if m:
        return _RHN_BASE + m.group(1)
    # Also look for direct file links
    m2 = re.search(r'href="(https?://[^"]+\.(?:ips|bps|zip|xdelta)[^"]*)"',
                   html, re.IGNORECASE)
    return m2.group(1) if m2 else ""


def search_romhacking(
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[PatchCandidate]:
    """Search romhacking.net translations + hacks for a PS1 game."""
    def _log(m): progress_cb and progress_cb(m)
    name    = clean_title(game_name)
    query   = urllib.parse.quote_plus(name)
    results = []

    for label, url_tmpl in [("romhacking-trans", _RHN_SEARCH),
                             ("romhacking-hack",  _RHN_HACKS)]:
        url  = url_tmpl.format(query=query)
        html = _http_text(url, timeout=15)
        if html:
            found = _rhn_parse_results(html, label)
            results.extend(found)

    _log(f"romhacking.net: {len(results)} result(s) for '{name}'")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 2 — Archive.org (patch file search)
# ═══════════════════════════════════════════════════════════════════════════════

_ARCH_PATCH_SEARCH = (
    "https://archive.org/advancedsearch.php"
    "?q={query}&fl[]=identifier&fl[]=title&fl[]=mediatype"
    "&output=json&rows=6&mediatype=software"
)


def search_archive_patches(
    game_name: str,
    serial: str = "",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[PatchCandidate]:
    """Search archive.org for IPS/BPS patch collections."""
    def _log(m): progress_cb and progress_cb(m)
    name    = clean_title(game_name)
    results = []

    for q_suffix in ["ips patch translation", "english patch psx", "translation patch"]:
        q    = urllib.parse.quote_plus(f"{name} {q_suffix}")
        raw  = _http_get(_ARCH_PATCH_SEARCH.format(query=q), timeout=15)
        if not raw:
            continue
        try:
            docs = json.loads(raw).get("response", {}).get("docs", [])
        except Exception:
            continue
        name_words = [w for w in name.lower().split() if len(w) > 2]
        for doc in docs:
            ident = doc.get("identifier", "")
            title = doc.get("title", ident)
            if any(w in title.lower() for w in name_words):
                # Fetch item metadata to find .ips/.bps files
                meta_raw = _http_get(
                    f"https://archive.org/metadata/{ident}", timeout=12)
                if not meta_raw:
                    continue
                try:
                    meta  = json.loads(meta_raw)
                    files = meta.get("files", [])
                    for f in files:
                        fname = f.get("name", "")
                        ext   = os.path.splitext(fname)[1].lower()
                        if ext in (".ips", ".bps", ".xdelta", ".zip"):
                            dl_url = (f"https://archive.org/download/"
                                      f"{ident}/{urllib.parse.quote(fname)}")
                            results.append(PatchCandidate(
                                source     = "archive",
                                title      = f"{title} — {fname}",
                                url        = dl_url,
                                patch_type = ext.lstrip("."),
                                game_title = name,
                            ))
                except Exception:
                    pass
        if results:
            break

    _log(f"Archive.org patches: {len(results)} file(s) for '{name}'")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 3 — PSX-Place (tag pages)
# ═══════════════════════════════════════════════════════════════════════════════

_PSXP_TAGS = [
    "https://www.psx-place.com/tags/english-patch/",
    "https://www.psx-place.com/tags/ps1-patches/",
    "https://www.psx-place.com/tags/translation/",
]


def search_psxplace(
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[PatchCandidate]:
    """
    Scrape PSX-Place tag pages for matching game patches.
    Returns threads whose title contains words from game_name.
    """
    def _log(m): progress_cb and progress_cb(m)
    name       = clean_title(game_name).lower()
    name_words = [w for w in name.split() if len(w) > 2]
    results    = []
    seen       = set()

    for tag_url in _PSXP_TAGS:
        html = _http_text(tag_url, timeout=12)
        if not html:
            continue
        # Thread links: <a href="https://www.psx-place.com/threads/SLUG.ID/">TITLE</a>
        threads = re.findall(
            r'href="(https://www\.psx-place\.com/threads/[^"]+)"[^>]*>\s*([^<]+)',
            html, re.IGNORECASE)
        for url, title in threads:
            title = title.strip()
            if not title or url in seen:
                continue
            tl = title.lower()
            if any(w in tl for w in name_words):
                seen.add(url)
                results.append(PatchCandidate(
                    source      = "psxplace",
                    title       = title,
                    url         = url,     # thread page — user visits for download
                    patch_type  = "unknown",
                    description = "PSX-Place thread (visit for download link)",
                ))

    _log(f"PSX-Place: {len(results)} thread(s) for '{name}'")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED SEARCH + DOWNLOAD API
# ═══════════════════════════════════════════════════════════════════════════════

def search_patches(
    game_name:   str,
    serial:      str = "",
    sources:     Optional[List[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[PatchCandidate]:
    """
    Search all configured sources and return combined list of PatchCandidates.

    sources: list of "romhacking" | "archive" | "psxplace"
             Default: all three.
    """
    def _log(m): progress_cb and progress_cb(m)
    if sources is None:
        sources = ["romhacking", "archive", "psxplace"]

    all_results: List[PatchCandidate] = []
    for src in sources:
        src = src.lower()
        if src == "romhacking":
            all_results += search_romhacking(game_name, progress_cb)
        elif src == "archive":
            all_results += search_archive_patches(game_name, serial, progress_cb)
        elif src in ("psxplace", "psx-place"):
            all_results += search_psxplace(game_name, progress_cb)

    _log(f"Total patch candidates: {len(all_results)}")
    return all_results


def download_patch(
    candidate:   PatchCandidate,
    serial:      str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Download a PatchCandidate and cache it locally.
    Sets candidate.local_path on success. Returns True on success.
    """
    def _log(m): progress_cb and progress_cb(m)

    url = candidate.url

    # romhacking.net detail pages need one more hop to get the actual file URL
    if candidate.source.startswith("romhacking") and "/translations/" in url or "/hacks/" in url:
        _log(f"Resolving romhacking.net download link…")
        url = _rhn_resolve_download(url)
        if not url:
            _log("Could not resolve direct download link from romhacking.net.")
            return False

    # Determine filename
    url_path = urllib.parse.urlparse(url).path
    fname    = os.path.basename(url_path) or f"patch_{serial}.ips"
    if not os.path.splitext(fname)[1]:
        fname += ".ips"

    dest = _cached_patch(serial, fname)
    if os.path.isfile(dest):
        _log(f"Using cached patch: {fname}")
        candidate.local_path = dest
        return True

    _log(f"Downloading patch: {fname}…")
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        if os.path.isfile(dest) and os.path.getsize(dest) > 8:
            _log(f"Downloaded {os.path.getsize(dest):,} bytes → {fname}")
            candidate.local_path = dest
            # Update patch_type from actual file
            candidate.patch_type = detect_patch_format(dest)
            return True
    except Exception as e:
        _log(f"Download error: {e}")

    return False


def apply_patches_to_iso(
    iso_path:    str,
    patches:     List[PatchCandidate],
    out_dir:     str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Apply a list of downloaded PatchCandidates to *iso_path* in sequence.
    Returns the path to the patched ISO (in *out_dir*) or original path if nothing applied.
    """
    def _log(m): progress_cb and progress_cb(m)

    applicable = [p for p in patches
                  if p.local_path and os.path.isfile(p.local_path)
                  and p.patch_type not in ("unknown", "psxplace")]
    if not applicable:
        return iso_path

    current = iso_path
    for i, patch in enumerate(applicable, 1):
        out = os.path.join(out_dir, f"patched_{i}_{os.path.basename(iso_path)}")
        _log(f"Applying patch {i}/{len(applicable)}: {patch.title[:50]}…")
        ok = patch_iso(current, patch.local_path, out, progress_cb)
        if ok:
            current = out
            _log(f"  Patch {i} applied.")
        else:
            _log(f"  Patch {i} FAILED — skipping.")

    return current


def xdelta3_available() -> bool:
    return bool(_find_xdelta3())
