"""
bgm.py – BGM search, multi-result collection, download, and AT3 conversion.

Search order:
  1. KH Insider  – proper HTML search + all-result collection
  2. Internet Archive – search API + file listing
  3. YouTube via yt-dlp

Key design: search functions return ALL matching candidates so the GUI
can present a pick-list.  The caller passes an optional pick_cb(candidates)
that returns the chosen candidate index (or -1 to skip).
"""

import os
import re
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Callable, List

from .constants import (
    AT3TOOL_EXE, LAME_EXE, CACHE_DIR, AT3_BITRATE, ROOT_DIR,
    KHI_BASE, KHI_SEARCH, KHI_ALBUM,
    ARCHIVE_SEARCH, ARCHIVE_META, ARCHIVE_DOWNLOAD,
)

# ── HTTP ──────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://downloads.khinsider.com/",
}


def _http_get(url: str, timeout: int = 18) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read() if r.status == 200 else None
    except Exception:
        return None


def _http_text(url: str, timeout: int = 18) -> str:
    raw = _http_get(url, timeout)
    return raw.decode("utf-8", errors="ignore") if raw else ""


# ── Title cleaner ─────────────────────────────────────────────────────────────

# Patterns found in gameInfo.db titles:
#   "Pixygarden [NTSC-J] [Disc1of2]"
#   "Castlevania - Symphony of the Night [NTSC-U]"
#   "Final Fantasy VII [PAL-E] [Disc1of3]"
#   "Darkstalkers 3 - Vampire Savior EX Edition [NTSC-J]"

_NOISE_PATTERNS = [
    r"\[NTSC[^\]]*\]",        # [NTSC-U] [NTSC-J] [NTSC]
    r"\[PAL[^\]]*\]",         # [PAL-E] [PAL]
    r"\[Disc\d+of\d+\]",      # [Disc1of2] [Disc1of3]
    r"\[Disc\s*\d+\]",        # [Disc 1]
    r"\(Disc\s*\d+\)",         # (Disc 1)
    r"\(English[^)]*\)",       # (English v1.3)
    r"\(USA\)|\(Japan\)|\(Europe\)|\(World\)",
    r"\(Rev\s*[A-Z0-9\.]+\)",  # (Rev A) (Rev 1.1)
    r"\(v[\d\.]+\)",           # (v1.3)
    r"\[NTSC-U\]|\[NTSC-J\]|\[PAL\]|\[PAL-E\]",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


def clean_title(raw: str) -> str:
    """
    Strip region tags, disc numbers, version strings and other noise from
    a gameInfo.db title so it can be used as a search query.

    Examples:
        "Pixygarden [NTSC-J] [Disc1of2]"            → "Pixygarden"
        "Castlevania - Symphony of the Night [NTSC-U]"
            → "Castlevania - Symphony of the Night"
        "Final Fantasy VII [PAL-E] [Disc1of3]"       → "Final Fantasy VII"
    """
    # Remove noise brackets/parens
    cleaned = _NOISE_RE.sub("", raw)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned

# ── subprocess runner ─────────────────────────────────────────────────────────

def _run(cmd: list, cwd: Optional[str] = None, timeout: int = 120) -> bool:
    try:
        if sys.platform == "win32":
            quoted = " ".join(
                f'"{a}"' if (" " in str(a) or not str(a)) else str(a) for a in cmd)
            result = subprocess.run(quoted, cwd=cwd, shell=True, timeout=timeout,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            result = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _ytdlp_available() -> bool:
    try:
        import yt_dlp; return True  # noqa: F401,E401
    except ImportError:
        return False

def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def _at3tool_available() -> bool:
    return os.path.isfile(AT3TOOL_EXE)

def _lame_available() -> bool:
    return os.path.isfile(LAME_EXE)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BgmCandidate:
    """One searchable BGM option the user can choose from."""
    source:      str          # "khinsider" | "archive" | "youtube"
    title:       str          # album / video title
    track_name:  str          # individual track name
    year:        str   = ""
    platform:    str   = ""
    slug:        str   = ""   # KHI album slug or archive identifier
    track_path:  str   = ""   # KHI relative path or archive filename
    direct_url:  str   = ""   # pre-resolved download URL (if available)

    def label(self) -> str:
        parts = [self.title]
        if self.track_name and self.track_name != self.title:
            parts.append(f"— {self.track_name}")
        if self.platform:
            parts.append(f"[{self.platform}]")
        if self.year:
            parts.append(f"({self.year})")
        parts.append(f"[{self.source}]")
        return "  ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# KH INSIDER — search returns all matching albums + track choices
# ═══════════════════════════════════════════════════════════════════════════════

def _khi_slug_candidates(game_name: str) -> List[str]:
    """
    Generate every plausible KHI album slug from a game title.
    KHI slugs are typically:  lowercase-name[-subtitle]-YYYY
    We try many variations to maximise hit rate without a search round-trip.
    """
    raw  = clean_title(game_name).lower()
    # Strip everything after common subtitle separators
    raw  = re.split(r"\s*[:\-–—|]\s*", raw)[0].strip()
    # Keep only alphanumeric + spaces
    raw  = re.sub(r"[^a-z0-9\s]", "", raw)
    base = re.sub(r"\s+", "-", raw.strip())

    # Also try removing stopwords to match compact slugs
    compact = re.sub(r"\b(the|a|an|of|in|and|for|to|from|on|at|by)\b", "", base)
    compact = re.sub(r"-+", "-", compact).strip("-")

    # Year suffixes (1994-2001 covers most PS1 era)
    years   = [str(y) for y in range(2001, 1993, -1)]

    candidates = []
    for b in dict.fromkeys([base, compact]):
        for suffix in (
            "", "-psx", "-ps1", "-psx-gamerip", "-ps1-gamerip",
            "-ost", "-original-soundtrack", "-soundtrack",
            "-sound-track", "-psx-ost",
        ):
            candidates.append(b + suffix)
        for y in years:
            candidates.append(f"{b}-{y}")
            candidates.append(f"{b}-sound-track-{y}")
            candidates.append(f"{b}-psx-{y}")
    return list(dict.fromkeys(candidates))  # deduplicate, preserve order


def _khi_search(game_name: str,
                progress_cb: Optional[Callable[[str], None]] = None) -> List[BgmCandidate]:
    """
    Search KH Insider and return all PS1/PSX album candidates found.
    Uses the real KHI search page and extracts album hrefs from full HTML.
    """
    def _log(m): progress_cb and progress_cb(m)
    game_name  = clean_title(game_name)          # strip [NTSC-J], [Disc1of2] etc.
    candidates: List[BgmCandidate] = []

    # ── 1. Use KHI search page to get actual hrefs ────────────────────────────
    # Try with "psx" keyword first, then plain game name
    for query in [f"{game_name} psx", f"{game_name} ps1", game_name]:
        q    = urllib.parse.quote_plus(query)
        html = _http_text(KHI_SEARCH.format(query=q))
        if not html:
            continue

        # Extract: href="/game-soundtracks/album/SLUG"
        hrefs = re.findall(r'href="(/game-soundtracks/album/([\w\-]+))"', html)
        # Also grab adjacent metadata (platform, year) from surrounding table row
        # KHI search rows look like:  ALBUMNAME\n PS1 \n Soundtrack \n 1999
        rows = re.findall(
            r'/game-soundtracks/album/([\w\-]+)"[^>]*>([^<]+)<.*?'
            r'(?:PS1|PSX|PlayStation)[^<]*(?:<[^>]+>)*\s*([A-Za-z]+)\s*'
            r'(?:<[^>]+>)*\s*(\d{4})?',
            html, re.DOTALL | re.IGNORECASE)

        seen_slugs = set()
        for _, slug in hrefs:
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            # Extract title from slug for display
            title = slug.replace("-", " ").title()
            year  = re.search(r"-(\d{4})$", slug)
            y     = year.group(1) if year else ""
            candidates.append(BgmCandidate(
                source="khinsider", title=title,
                track_name="(all tracks)", year=y, platform="PS1", slug=slug))

        if candidates:
            _log(f"KH Insider: found {len(candidates)} album(s) for '{query}'")
            break

    # ── 2. Also try direct slug hits for extra coverage ───────────────────────
    if len(candidates) < 3:
        for slug in _khi_slug_candidates(game_name):
            if any(c.slug == slug for c in candidates):
                continue
            html = _http_text(KHI_ALBUM.format(slug=slug))
            if html and "Total Filesize" in html:
                title = slug.replace("-", " ").title()
                year  = re.search(r"-(\d{4})", slug)
                y     = year.group(1) if year else ""
                candidates.append(BgmCandidate(
                    source="khinsider", title=title,
                    track_name="(all tracks)", year=y, platform="PS1", slug=slug))
                _log(f"KH Insider: direct slug match '{slug}'")
            if len(candidates) >= 10:
                break

    return candidates


def _khi_get_tracks(slug: str) -> List[tuple]:
    """
    Return list of (track_name, relative_path) tuples for a KHI album.
    """
    html = _http_text(KHI_ALBUM.format(slug=slug))
    if not html:
        return []
    links = re.findall(
        rf'href="(/game-soundtracks/album/{re.escape(slug)}/([^"]+\.mp3))"',
        html, re.IGNORECASE)
    # Deduplicate preserving order
    seen, out = set(), []
    for path, fname in links:
        if path not in seen:
            seen.add(path)
            name = urllib.parse.unquote(fname).replace("%20", " ").replace("+", " ")
            out.append((name, path))
    return out


def _khi_best_track(tracks: List[tuple]) -> Optional[tuple]:
    """Pick the most 'menu/title/theme' track from a list."""
    priority = ["title", "main", "opening", "theme", "prologue",
                "intro", "menu", "select", "01"]
    for kw in priority:
        for name, path in tracks:
            if kw in name.lower():
                return (name, path)
    return tracks[0] if tracks else None


def _khi_resolve_direct_url(track_path: str) -> Optional[str]:
    """
    Fetch a KHI individual track page and extract the direct CDN MP3 URL.
    """
    html = _http_text(KHI_BASE + track_path)
    if not html:
        return None
    m = re.search(r'href="(https://[^"]+\.mp3)"', html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'src="(https://[^"]+\.mp3)"', html, re.IGNORECASE)
    return m.group(1) if m else None


def _khi_download_candidate(candidate: BgmCandidate, dest_mp3: str,
                             progress_cb: Optional[Callable[[str], None]] = None,
                             track_index: int = 0) -> bool:
    """
    Download a specific KHI candidate.  track_index=0 picks best track automatically.
    """
    def _log(m): progress_cb and progress_cb(m)

    tracks = _khi_get_tracks(candidate.slug)
    if not tracks:
        _log(f"KH Insider: no tracks found for '{candidate.slug}'")
        return False

    if 0 < track_index <= len(tracks):
        name, path = tracks[track_index - 1]
    else:
        name, path = _khi_best_track(tracks) or tracks[0]

    _log(f"KH Insider: resolving '{name}'…")
    direct_url = _khi_resolve_direct_url(path)
    if not direct_url:
        _log("KH Insider: could not resolve direct MP3 URL")
        return False

    _log(f"KH Insider: downloading…")
    return _stream_download(direct_url, dest_mp3, progress_cb)


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNET ARCHIVE — search returns all matching items
# ═══════════════════════════════════════════════════════════════════════════════

def _archive_search(game_name: str,
                    progress_cb: Optional[Callable[[str], None]] = None) -> List[BgmCandidate]:
    """Search archive.org and return all matching audio items as candidates."""
    def _log(m): progress_cb and progress_cb(m)
    game_name  = clean_title(game_name)          # strip noise
    candidates: List[BgmCandidate] = []

    for query_suffix in ["psx soundtrack", "ps1 soundtrack", "soundtrack"]:
        q    = urllib.parse.quote_plus(f"{game_name} {query_suffix}")
        url  = ARCHIVE_SEARCH.format(query=q)
        raw  = _http_get(url, timeout=15)
        if not raw:
            continue
        try:
            docs = json.loads(raw).get("response", {}).get("docs", [])
        except Exception:
            continue
        name_words = [w for w in game_name.lower().split() if len(w) > 2]
        for doc in docs:
            ident = doc.get("identifier", "")
            title = doc.get("title", ident)
            t_lc  = title.lower()
            if any(w in t_lc for w in name_words):
                if not any(c.slug == ident for c in candidates):
                    candidates.append(BgmCandidate(
                        source="archive", title=title,
                        track_name="(best match)", slug=ident))
        if candidates:
            _log(f"Archive.org: {len(candidates)} item(s) found")
            break

    return candidates


def _archive_get_mp3s(identifier: str) -> List[tuple]:
    """Return [(filename, url)] for all MP3s in an archive item."""
    raw = _http_get(ARCHIVE_META.format(identifier=identifier), timeout=15)
    if not raw:
        return []
    try:
        files = json.loads(raw).get("files", [])
        out   = []
        for f in files:
            name = f.get("name", "")
            if name.lower().endswith(".mp3"):
                url = ARCHIVE_DOWNLOAD.format(
                    identifier=identifier,
                    filename=urllib.parse.quote(name))
                out.append((name, url))
        return out
    except Exception:
        return []


def _archive_best_mp3(mp3s: List[tuple]) -> Optional[tuple]:
    priority = ["title", "main", "theme", "opening", "intro", "menu", "01"]
    for kw in priority:
        for name, url in mp3s:
            if kw in name.lower():
                return (name, url)
    return mp3s[0] if mp3s else None


def _archive_download_candidate(candidate: BgmCandidate, dest_mp3: str,
                                 progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    def _log(m): progress_cb and progress_cb(m)
    mp3s = _archive_get_mp3s(candidate.slug)
    if not mp3s:
        _log(f"Archive.org: no MP3s in '{candidate.slug}'")
        return False
    name, url = (candidate.direct_url and ("", candidate.direct_url)
                 or _archive_best_mp3(mp3s) or ("", ""))
    if not url:
        return False
    _log(f"Archive.org: downloading '{name}'…")
    return _stream_download(url, dest_mp3, progress_cb)


# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE via yt-dlp
# ═══════════════════════════════════════════════════════════════════════════════

def _ytdlp_search(game_name: str,
                  progress_cb: Optional[Callable[[str], None]] = None) -> List[BgmCandidate]:
    """Return up to 5 YouTube candidates (using ytsearch5)."""
    if not _ytdlp_available():
        return []
    try:
        import yt_dlp
    except ImportError:
        return []
    def _log(m): progress_cb and progress_cb(m)
    game_name = clean_title(game_name)           # strip noise
    query = f"{game_name} PSX PS1 OST soundtrack"
    _log(f"YouTube: searching '{query}'…")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                               "extract_flat": True, "default_search": "ytsearch5"}) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        entries = (info or {}).get("entries", []) or []
        return [BgmCandidate(
            source="youtube", title=e.get("title", "?"),
            track_name=e.get("title", "?"),
            year=str(e.get("upload_date", "")[:4] if e.get("upload_date") else ""),
            direct_url=e.get("url") or e.get("webpage_url") or e.get("id", ""))
            for e in entries[:5] if e]
    except Exception as e:
        _log(f"YouTube search error: {e}")
        return []


def _ytdlp_download_candidate(candidate: BgmCandidate, dest_mp3: str,
                               progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    if not _ytdlp_available():
        return False
    try:
        import yt_dlp
    except ImportError:
        return False
    def _log(m): progress_cb and progress_cb(m)
    url      = candidate.direct_url
    tmp_base = dest_mp3.replace(".mp3", "_ytdl")

    # When ffmpeg is available: download best audio and convert to mp3.
    # When ffmpeg is absent: restrict to formats lame can decode (mp3, wav).
    # m4a works with a direct rename trick via mutagen if needed.
    if _ffmpeg_available():
        fmt = "bestaudio/best"
        postproc = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                     "preferredquality": "192"}]
    else:
        # Only download formats that lame --decode can handle natively
        fmt = "bestaudio[ext=mp3]/worstvideo[ext=mp3]/bestaudio[ext=m4a]/bestaudio"
        postproc = []

    ydl_opts = {
        "format":          fmt,
        "outtmpl":         tmp_base + ".%(ext)s",
        "noplaylist":      True,
        "quiet":           True,
        "no_warnings":     True,
        "socket_timeout":  30,
        "extractor_retries": 2,
        "postprocessors":  postproc,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # Find the downloaded file — prefer mp3 first
        for ext in ("mp3", "m4a", "wav", "webm", "opus", "ogg"):
            cand = tmp_base + "." + ext
            if os.path.isfile(cand) and os.path.getsize(cand) > 50_000:
                if ext in ("webm", "opus", "ogg") and not _ffmpeg_available():
                    _log(f"YouTube: got {ext.upper()} but ffmpeg not available "
                         f"for conversion — skipping.")
                    os.unlink(cand)
                    return False
                shutil.move(cand, dest_mp3)
                _log(f"YouTube: downloaded {os.path.getsize(dest_mp3)//1024} KB "
                     f"({ext.upper()})")
                return True
    except Exception as e:
        _log(f"YouTube download error: {e}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED DOWNLOAD HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _stream_download(url: str, dest: str,
                     progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    """Stream-download *url* to *dest* file.  Returns True on success."""
    def _log(m): progress_cb and progress_cb(m)
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            done  = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and progress_cb:
                    pct = int(done / total * 100)
                    progress_cb(f"  {pct}% ({done//1024}/{total//1024} KB)")
        if os.path.isfile(dest) and os.path.getsize(dest) > 50_000:
            return True
    except Exception as e:
        _log(f"Download error: {e}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# AT3 CONVERSION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def _mp3_to_wav(mp3: str, wav: str) -> bool:
    return _lame_available() and _run(
        [LAME_EXE, "--decode", "--quiet", mp3, wav], timeout=120)

def _audio_to_wav(src: str, wav: str) -> bool:
    return _ffmpeg_available() and _run(
        ["ffmpeg", "-y", "-i", src, "-ar", "44100", "-ac", "2",
         "-sample_fmt", "s16", wav], timeout=120)

def _ensure_wav_format(wav_in: str, wav_out: str) -> bool:
    if _ffmpeg_available():
        return _run(["ffmpeg", "-y", "-i", wav_in, "-ar", "44100", "-ac", "2",
                     "-sample_fmt", "s16", wav_out], timeout=60)
    if wav_in != wav_out:
        shutil.copy2(wav_in, wav_out)
    return True

def _wav_to_at3(wav: str, at3: str, bitrate: int = AT3_BITRATE, loop: bool = True) -> bool:
    if not _at3tool_available() or not os.path.isfile(wav):
        return False
    cmd = [AT3TOOL_EXE, "-e", "-br", str(bitrate)]
    if loop:
        cmd += ["-wholeloop"]
    cmd += [wav, at3]
    return _run(cmd, cwd=str(ROOT_DIR), timeout=180)


def _get_audio_ext(path: str) -> str:
    """
    Detect actual audio format by magic bytes, not just extension.
    Returns lowercase extension: 'mp3', 'ogg', 'flac', 'm4a', 'wav', 'webm', etc.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(12)
    except OSError:
        return os.path.splitext(path)[1].lower().lstrip(".")

    if header[:3] == b"\xff\xfb" or header[:3] == b"\xff\xf3" or header[:3] == b"\xff\xf2":
        return "mp3"
    if header[:3] == b"ID3":
        return "mp3"
    if header[:4] == b"fLaC":
        return "flac"
    if header[:4] == b"OggS":
        return "ogg"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[4:8] == b"ftyp":
        return "m4a"
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    # Fall back to extension
    return os.path.splitext(path)[1].lower().lstrip(".") or "mp3"


def _to_at3(audio: str, at3: str, loop: bool = True,
            progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    """
    Convert any local audio file to AT3.
    Uses magic-byte detection so renamed files (e.g. .mp3 that is really webm)
    are handled correctly.
    """
    def _log(m): progress_cb and progress_cb(m)

    actual_ext = _get_audio_ext(audio)
    _log(f"Audio format detected: {actual_ext.upper()}")

    with tempfile.TemporaryDirectory(prefix="psx2psp_at3_") as tmp:
        wav = os.path.join(tmp, "ready.wav")

        if actual_ext == "wav":
            _log("Resampling WAV to 44100/16/stereo…")
            ok = _ensure_wav_format(audio, wav)

        elif actual_ext == "mp3":
            if _ffmpeg_available():
                _log("Converting MP3 → WAV via ffmpeg…")
                ok = _audio_to_wav(audio, wav)
            elif _lame_available():
                _log("Decoding MP3 → WAV via lame…")
                ok = _mp3_to_wav(audio, wav)
            else:
                _log("No MP3 decoder available (need lame or ffmpeg).")
                ok = False

        elif actual_ext in ("ogg", "flac", "webm", "opus", "m4a"):
            if _ffmpeg_available():
                _log(f"Converting {actual_ext.upper()} → WAV via ffmpeg…")
                ok = _audio_to_wav(audio, wav)
            else:
                _log(f"{actual_ext.upper()} requires ffmpeg for conversion. "
                     f"Install ffmpeg or use an MP3 file instead.")
                ok = False

        else:
            # Unknown format — try ffmpeg first, then lame as last resort
            if _ffmpeg_available():
                _log(f"Converting unknown format → WAV via ffmpeg…")
                ok = _audio_to_wav(audio, wav)
            elif _lame_available():
                _log("Trying lame decode on unknown format…")
                ok = _mp3_to_wav(audio, wav)
            else:
                ok = False

        if not ok or not os.path.isfile(wav):
            _log("WAV conversion failed — cannot produce AT3.")
            return False

        _log(f"Encoding → AT3 ({AT3_BITRATE} kbps)…")
        ok = _wav_to_at3(wav, at3, bitrate=AT3_BITRATE, loop=loop)
        if ok:
            _log(f"AT3 ready: {os.path.basename(at3)}")
        else:
            _log("AT3 encoding failed (check at3tool.exe path).")
        return ok


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def search_bgm(
    game_name: str,
    sources: Optional[List[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[BgmCandidate]:
    """
    Search all configured sources and return a combined list of BgmCandidates.
    The caller (GUI) shows these to the user for selection.
    """
    def _log(m): progress_cb and progress_cb(m)
    if sources is None:
        sources = ["khinsider", "archive", "youtube"]
    game_name = clean_title(game_name)          # strip [NTSC-J] [Disc1of2] etc.
    _log(f"Searching BGM for: '{game_name}'")
    all_candidates: List[BgmCandidate] = []
    for src in sources:
        src = src.lower()
        if src == "khinsider":
            all_candidates += _khi_search(game_name, progress_cb)
        elif src == "archive":
            all_candidates += _archive_search(game_name, progress_cb)
        elif src in ("youtube", "yt-dlp", "ytdlp"):
            all_candidates += _ytdlp_search(game_name, progress_cb)
    _log(f"Total BGM candidates found: {len(all_candidates)}")
    return all_candidates


def download_bgm_candidate(
    candidate: BgmCandidate,
    dest_at3: str,
    loop: bool = True,
    progress_cb: Optional[Callable[[str], None]] = None,
    track_index: int = 0,
) -> bool:
    """
    Download and convert a specific BgmCandidate to AT3.
    track_index: 0 = auto-pick best track; 1..N = specific track number.
    """
    def _log(m): progress_cb and progress_cb(m)
    os.makedirs(os.path.dirname(dest_at3) or ".", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="psx2psp_bgm_") as tmp:
        mp3 = os.path.join(tmp, "bgm.mp3")
        ok  = False

        if candidate.source == "khinsider":
            ok = _khi_download_candidate(candidate, mp3, progress_cb, track_index)
        elif candidate.source == "archive":
            ok = _archive_download_candidate(candidate, mp3, progress_cb)
        elif candidate.source == "youtube":
            ok = _ytdlp_download_candidate(candidate, mp3, progress_cb)

        if not ok or not os.path.isfile(mp3):
            _log("Download failed.")
            return False

        return _to_at3(mp3, dest_at3, loop=loop, progress_cb=progress_cb)


def get_khi_tracks(candidate: BgmCandidate) -> List[tuple]:
    """Return track list [(name, path)] for a KHI candidate (for track picker)."""
    if candidate.source != "khinsider":
        return []
    return _khi_get_tracks(candidate.slug)


def search_and_get_bgm(
    game_name: str,
    serial: str,
    dest_at3: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    custom_audio_path: Optional[str] = None,
    loop: bool = True,
    sources: Optional[List[str]] = None,
    pick_cb: Optional[Callable[[List[BgmCandidate]], int]] = None,
) -> bool:
    """
    Full pipeline:
      1. If custom_audio_path → convert directly.
      2. Check cache.
      3. Search all sources, collect candidates.
      4. If pick_cb provided → call it to let the user choose; else auto-pick first.
      5. Download + convert chosen candidate to AT3.

    pick_cb(candidates) should return the 0-based index to use, or -1 to skip.
    """
    def _log(m): progress_cb and progress_cb(m)
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Custom audio
    if custom_audio_path and os.path.isfile(custom_audio_path):
        _log(f"Converting custom audio: {os.path.basename(custom_audio_path)}…")
        ok = convert_to_at3(custom_audio_path, dest_at3, progress_cb, loop)
        if ok:
            shutil.copy2(dest_at3, os.path.join(CACHE_DIR, f"bgm_{serial}.at3"))
        return ok

    # Cache
    cache_at3 = os.path.join(CACHE_DIR, f"bgm_{serial}.at3")
    if os.path.isfile(cache_at3):
        _log(f"Using cached BGM for {serial}.")
        shutil.copy2(cache_at3, dest_at3)
        return True

    # Search
    candidates = search_bgm(game_name, sources, progress_cb)
    if not candidates:
        _log("No BGM candidates found from any source.")
        return False

    # Pick
    if pick_cb:
        idx = pick_cb(candidates)
        if idx < 0:
            _log("BGM selection cancelled.")
            return False
        idx = min(idx, len(candidates) - 1)
    else:
        idx = 0  # auto: first result

    chosen = candidates[idx]
    _log(f"Using: {chosen.label()}")
    ok = download_bgm_candidate(chosen, dest_at3, loop, progress_cb)
    if ok:
        shutil.copy2(dest_at3, cache_at3)
    return ok


def convert_to_at3(
    source_audio: str,
    at3_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    loop: bool = True,
) -> bool:
    """Convert a local audio file to SND0.AT3."""
    if not os.path.isfile(source_audio):
        if progress_cb:
            progress_cb(f"Source not found: {source_audio}")
        return False
    if not _at3tool_available():
        if progress_cb:
            progress_cb("at3tool.exe not found.")
        return False
    return _to_at3(source_audio, at3_path, loop=loop, progress_cb=progress_cb)


def get_bgm_sources() -> dict:
    return {
        "khinsider":   True,
        "archive.org": True,
        "yt_dlp":      _ytdlp_available(),
        "ffmpeg":      _ffmpeg_available(),
        "at3tool":     _at3tool_available(),
        "lame":        _lame_available(),
    }
