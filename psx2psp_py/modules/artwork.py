"""
artwork.py – Online artwork search, download, and PSP image generation.

Cover art sources (tried in order):
  1. xlenore/psx-covers     – GitHub raw, serial-based, no auth, most reliable
  2. Libretro Named_Boxarts  – game-name based, no auth
  3. TheGamesDB v2 API       – optional TGDB_API_KEY env var for higher limits
  4. Sony SCE TMDB           – serial-based PlayStation metadata
  5. DuckDuckGo image search – last-resort web scrape

Screenshot sources (for PIC1/BOOT backgrounds):
  1. Libretro Named_Snaps    – in-game screenshots
  2. Libretro Named_Titles   – title screen shots
  3. TheGamesDB screenshots  – if API key available
  4. PSX Data Center         – HTML scrape of game page

PSP output files generated:
  ICON0.PNG  144×80   – cover thumbnail (crisp fit, no bars if possible)
  PIC0.PNG   480×272  – XMB overlay: cover right, title+info left, transparent bg
  PIC1.PNG   480×272  – XMB background: blurred/darkened screenshot or cover
  BOOT.PNG   480×272  – boot splash: full-bleed cover with vignette + title text
"""

import io
import os
import re
import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, Callable, List

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageOps

from .constants import (
    CACHE_DIR, ASSETS_DIR, NO_ICON_PNG,
    COVER_URL_XLENORE, COVER_URL_XLENORE2,
    SCE_TMDB_META, LIBRETRO_THUMB, LIBRETRO_SNAP, LIBRETRO_TITLE,
    TGDB_SEARCH, TGDB_IMAGES, TGDB_IMG_BASE,
    PSXDC_GAME,
    ICON0_SIZE, PIC0_SIZE, PIC1_SIZE, BOOT_SIZE,
    CLR_BG, CLR_ACCENT, CLR_HILIGHT, CLR_FG, CLR_FG2,
)
from .bgm import clean_title


# ── Image candidate dataclass ─────────────────────────────────────────────────

from dataclasses import dataclass

@dataclass
class ImageCandidate:
    """One downloadable image option the user can choose from."""
    source:    str               # "xlenore" | "libretro" | "tgdb" | "tmdb" | "ddg" | "snap" | etc.
    url:       str               # direct image URL
    label:     str               # human-readable description
    category:  str = "cover"     # "cover" | "screenshot" | "title_screen"
    width:     int = 0
    height:    int = 0
    _cached_img: object = None   # PIL Image once fetched

    def display_label(self) -> str:
        dims = f" ({self.width}×{self.height})" if self.width else ""
        return f"[{self.source}] {self.label}{dims}"

# ── HTTP helpers ──────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch(url: str, timeout: int = 10) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception:
        pass
    return None


def _fetch_text(url: str, timeout: int = 10) -> str:
    raw = _fetch(url, timeout)
    return raw.decode("utf-8", errors="ignore") if raw else ""


def _fetch_image(url: str) -> Optional[Image.Image]:
    data = _fetch(url)
    if data:
        try:
            img = Image.open(io.BytesIO(data))
            # Verify it is a real image (not a 404 HTML page stored as bytes)
            img.verify()
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            if img.width > 10 and img.height > 10:
                return img
        except Exception:
            pass
    return None


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(key: str, ext: str = ".png") -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", key)
    return os.path.join(CACHE_DIR, safe + ext)


def _load_cached(key: str) -> Optional[Image.Image]:
    for ext in (".png", ".jpg"):
        p = _cache_path(key, ext)
        if os.path.isfile(p):
            try:
                img = Image.open(p).convert("RGBA")
                if img.width > 10:
                    return img
            except Exception:
                pass
    return None


def _save_cached(key: str, img: Image.Image) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _cache_path(key, ".png")
    img.save(p, "PNG")
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# COVER ART SOURCES
# ═══════════════════════════════════════════════════════════════════════════════

# ── Source 1 – xlenore PSX covers (GitHub raw) ────────────────────────────────

def _try_xlenore(serial: str) -> Optional[Image.Image]:
    hyphen = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", serial)
    for tmpl in (COVER_URL_XLENORE, COVER_URL_XLENORE2):
        for s in (serial, hyphen):
            img = _fetch_image(tmpl.format(serial=s))
            if img:
                return img
    return None


# ── Source 2 – Libretro Named_Boxarts ────────────────────────────────────────

def _libretro_safe(name: str) -> str:
    name = re.sub(r'[/\\:*?"<>|]', "_", name)
    return urllib.parse.quote(name)


def _try_libretro_cover(game_name: str) -> Optional[Image.Image]:
    game_name = clean_title(game_name)
    return _fetch_image(LIBRETRO_THUMB.format(name=_libretro_safe(game_name)))


# ── Source 3 – TheGamesDB v2 ──────────────────────────────────────────────────

def _tgdb_api_key() -> str:
    """Return configured TGDB API key or empty string."""
    return os.environ.get("TGDB_API_KEY", "").strip()


def _try_tgdb_cover(game_name: str) -> Optional[Image.Image]:
    """Search TheGamesDB for PSX (platform 10) and return the first boxart image."""
    key  = _tgdb_api_key()
    if not key:
        return None
    game_name = clean_title(game_name)
    q   = urllib.parse.quote(game_name)
    url = TGDB_SEARCH.format(name=q, key=key)
    raw = _fetch(url)
    if not raw:
        return None
    try:
        data    = json.loads(raw)
        games   = data.get("data", {}).get("games", [])
        if not games:
            return None
        gid     = games[0]["id"]
        boxart  = (data.get("include", {})
                       .get("boxart", {})
                       .get("data", {})
                       .get(str(gid), []))
        base    = (data.get("include", {})
                       .get("boxart", {})
                       .get("base_url", {})
                       .get("original", TGDB_IMG_BASE))
        for entry in boxart:
            if entry.get("side") in ("front", None):
                img = _fetch_image(base + entry["filename"])
                if img:
                    return img
    except Exception:
        pass
    return None


# ── Source 4 – Sony SCE TMDB ─────────────────────────────────────────────────

def _try_sce_tmdb(serial: str) -> Optional[Image.Image]:
    hyphen = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", serial)
    for sid in (serial, hyphen):
        raw = _fetch(SCE_TMDB_META.format(serial=sid))
        if not raw:
            continue
        try:
            meta  = json.loads(raw)
            icons = (meta.get("icons") or
                     meta.get("gameTitle", {}).get("icons") or [])
            for entry in icons:
                url = entry.get("icon") or entry.get("url") or ""
                if url:
                    img = _fetch_image(url)
                    if img:
                        return img
        except Exception:
            pass
    return None


# ── Source 5 – DuckDuckGo image scrape ───────────────────────────────────────

def _try_ddg_image(query: str) -> Optional[Image.Image]:
    q        = urllib.parse.quote_plus(query)
    html     = _fetch_text(f"https://duckduckgo.com/?q={q}&iax=images&ia=images")
    if not html:
        return None
    m = re.search(r'vqd=([\d-]+)', html)
    if not m:
        return None
    vqd  = m.group(1)
    api  = (f"https://duckduckgo.com/i.js?l=us-en&o=json&q={q}"
            f"&vqd={vqd}&f=,,,&p=1")
    raw  = _fetch(api)
    if not raw:
        return None
    try:
        for r in json.loads(raw).get("results", [])[:6]:
            img = _fetch_image(r.get("image") or r.get("thumbnail") or "")
            if img:
                return img
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT SOURCES  (used for PIC1 background / BOOT splash)
# ═══════════════════════════════════════════════════════════════════════════════

def _try_libretro_snap(game_name: str) -> Optional[Image.Image]:
    """In-game screenshot from Libretro Named_Snaps."""
    return _fetch_image(LIBRETRO_SNAP.format(name=_libretro_safe(clean_title(game_name))))


def _try_libretro_title(game_name: str) -> Optional[Image.Image]:
    """Title screen from Libretro Named_Titles."""
    return _fetch_image(LIBRETRO_TITLE.format(name=_libretro_safe(clean_title(game_name))))


def _try_tgdb_screenshot(game_name: str) -> Optional[Image.Image]:
    """First screenshot from TheGamesDB (requires TGDB_API_KEY)."""
    key = _tgdb_api_key()
    if not key:
        return None
    game_name = clean_title(game_name)
    q   = urllib.parse.quote(game_name)
    url = TGDB_SEARCH.format(name=q, key=key)
    raw = _fetch(url)
    if not raw:
        return None
    try:
        data  = json.loads(raw)
        games = data.get("data", {}).get("games", [])
        if not games:
            return None
        gid   = games[0]["id"]
        # Fetch screenshots for this game
        sraw  = _fetch(TGDB_IMAGES.format(gid=gid, key=key))
        if not sraw:
            return None
        sdata = json.loads(sraw)
        imgs  = (sdata.get("data", {})
                      .get("images", {})
                      .get(str(gid), []))
        base  = (sdata.get("data", {})
                      .get("base_url", {})
                      .get("original", TGDB_IMG_BASE))
        for entry in imgs:
            if entry.get("type") == "screenshot":
                img = _fetch_image(base + entry["filename"])
                if img:
                    return img
    except Exception:
        pass
    return None


def _try_psxdc_screenshot(serial_hyphen: str) -> Optional[Image.Image]:
    """
    Scrape PSX Data Center for the first screenshot image.
    serial_hyphen: e.g. SLUS-00067  (with hyphen)
    """
    # PSX Data Center URL uses region prefix: U=NTSC-U, E=PAL, J=NTSC-J
    region = "U"
    if serial_hyphen.startswith("SCES") or serial_hyphen.startswith("SLES"):
        region = "E"
    elif serial_hyphen.startswith("SLPM") or serial_hyphen.startswith("SCPS"):
        region = "J"
    url  = PSXDC_GAME.format(id=serial_hyphen)
    html = _fetch_text(url)
    if not html:
        return None
    # Find img src patterns like: /games/U/SLUS-00067/img/001.jpg
    imgs = re.findall(r'src="([^"]+\.(?:jpg|png|gif))"', html, re.IGNORECASE)
    for src in imgs:
        if "img" in src.lower() or "screen" in src.lower() or "shot" in src.lower():
            full = src if src.startswith("http") else "https://psxdatacenter.com" + src
            img  = _fetch_image(full)
            if img and img.width > 100:
                return img
    return None


def fetch_screenshot(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[Image.Image]:
    """Try all screenshot sources in order."""
    def _log(m): progress_cb and progress_cb(m)
    game_name = clean_title(game_name)

    key = f"shot_{serial}"
    cached = _load_cached(key)
    if cached:
        _log(f"Loaded screenshot from cache: {serial}")
        return cached

    _log("Fetching in-game screenshot (libretro snaps)…")
    img = _try_libretro_snap(game_name)
    if img:
        _log("Screenshot: libretro snap")
        _save_cached(key, img)
        return img

    _log("Fetching title screen (libretro titles)…")
    img = _try_libretro_title(game_name)
    if img:
        _log("Screenshot: libretro title screen")
        _save_cached(key, img)
        return img

    if _tgdb_api_key():
        _log("Fetching screenshot from TheGamesDB…")
        img = _try_tgdb_screenshot(game_name)
        if img:
            _log("Screenshot: TheGamesDB")
            _save_cached(key, img)
            return img

    hyphen = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", serial)
    _log("Fetching screenshot from PSX Data Center…")
    img = _try_psxdc_screenshot(hyphen)
    if img:
        _log("Screenshot: PSX Data Center")
        _save_cached(key, img)
        return img

    _log("No screenshot found; will use cover as background.")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# COVER ART ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def search_cover_candidates(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[ImageCandidate]:
    """Collect ALL available cover image URLs from every source."""
    def _log(m): progress_cb and progress_cb(m)
    game_name  = clean_title(game_name)    # strip [NTSC-J], [Disc1of2] etc.
    candidates: List[ImageCandidate] = []

    hyphen = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", serial)

    # xlenore — try all serial variants
    for tmpl_name, tmpl in [("xlenore-default", COVER_URL_XLENORE),
                             ("xlenore-plain",   COVER_URL_XLENORE2)]:
        for s in (serial, hyphen):
            url = tmpl.format(serial=s)
            candidates.append(ImageCandidate(
                source="xlenore", url=url,
                label=f"xlenore/{s}", category="cover"))

    # Libretro boxart
    if game_name:
        safe = _libretro_safe(game_name)
        candidates.append(ImageCandidate(
            source="libretro", url=LIBRETRO_THUMB.format(name=safe),
            label=game_name, category="cover"))

    # TheGamesDB
    if game_name and _tgdb_api_key():
        key = _tgdb_api_key()
        q   = urllib.parse.quote(game_name)
        raw = _fetch(TGDB_SEARCH.format(name=q, key=key))
        if raw:
            try:
                data   = json.loads(raw)
                games  = data.get("data", {}).get("games", [])
                base   = (data.get("include", {}).get("boxart", {})
                          .get("base_url", {}).get("original", TGDB_IMG_BASE))
                for g in games[:3]:
                    gid    = g["id"]
                    boxart = (data.get("include", {}).get("boxart", {})
                              .get("data", {}).get(str(gid), []))
                    for entry in boxart:
                        if entry.get("side") in ("front", None):
                            candidates.append(ImageCandidate(
                                source="tgdb", url=base + entry["filename"],
                                label=g.get("game_title", str(gid)), category="cover"))
            except Exception:
                pass

    # DDG fallback
    if game_name:
        q    = urllib.parse.quote_plus(f"{game_name} PS1 PSX cover art")
        html = _fetch_text(f"https://duckduckgo.com/?q={q}&iax=images&ia=images")
        m    = re.search(r'vqd=([\d-]+)', html)
        if m:
            api = f"https://duckduckgo.com/i.js?l=us-en&o=json&q={q}&vqd={m.group(1)}&f=,,,&p=1"
            raw = _fetch(api)
            if raw:
                try:
                    for r in json.loads(raw).get("results", [])[:4]:
                        img_url = r.get("image") or r.get("thumbnail") or ""
                        if img_url:
                            candidates.append(ImageCandidate(
                                source="duckduckgo", url=img_url,
                                label=f"DDG result", category="cover"))
                except Exception:
                    pass

    _log(f"Cover candidates found: {len(candidates)}")
    return candidates


def search_screenshot_candidates(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[ImageCandidate]:
    """Collect all available screenshot/title-screen URLs."""
    def _log(m): progress_cb and progress_cb(m)
    game_name  = clean_title(game_name)    # strip noise
    candidates: List[ImageCandidate] = []

    if game_name:
        safe = _libretro_safe(game_name)
        candidates.append(ImageCandidate(
            source="libretro-snap", url=LIBRETRO_SNAP.format(name=safe),
            label=f"{game_name} (snap)", category="screenshot"))
        candidates.append(ImageCandidate(
            source="libretro-title", url=LIBRETRO_TITLE.format(name=safe),
            label=f"{game_name} (title)", category="title_screen"))

    if _tgdb_api_key() and game_name:
        key = _tgdb_api_key()
        q   = urllib.parse.quote(game_name)
        raw = _fetch(TGDB_SEARCH.format(name=q, key=key))
        if raw:
            try:
                data  = json.loads(raw)
                games = data.get("data", {}).get("games", [])
                if games:
                    gid   = games[0]["id"]
                    sraw  = _fetch(TGDB_IMAGES.format(gid=gid, key=key))
                    if sraw:
                        sdata = json.loads(sraw)
                        imgs  = sdata.get("data", {}).get("images", {}).get(str(gid), [])
                        base  = sdata.get("data", {}).get("base_url", {}).get("original", TGDB_IMG_BASE)
                        for entry in imgs:
                            if entry.get("type") == "screenshot":
                                candidates.append(ImageCandidate(
                                    source="tgdb", url=base + entry["filename"],
                                    label="TheGamesDB screenshot", category="screenshot"))
            except Exception:
                pass

    hyphen = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", serial)
    html   = _fetch_text(PSXDC_GAME.format(id=hyphen))
    if html:
        for src in re.findall(r'src="([^"]+\.(?:jpg|png|gif))"', html, re.IGNORECASE):
            if "img" in src.lower() or "screen" in src.lower():
                full = src if src.startswith("http") else "https://psxdatacenter.com" + src
                candidates.append(ImageCandidate(
                    source="psxdatacenter", url=full,
                    label="PSX Data Center", category="screenshot"))
                if len([c for c in candidates if c.source == "psxdatacenter"]) >= 4:
                    break

    _log(f"Screenshot candidates found: {len(candidates)}")
    return candidates


def resolve_candidate(candidate: ImageCandidate) -> Optional[Image.Image]:
    """Fetch and return the image for a given candidate (with caching)."""
    if candidate._cached_img:
        return candidate._cached_img
    img = _fetch_image(candidate.url)
    if img:
        candidate._cached_img = img
        if candidate.width == 0:
            candidate.width  = img.width
            candidate.height = img.height
    return img


def fetch_cover_art(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[Image.Image]:
    """Try all cover sources in priority order; return first hit."""
    def _log(m): progress_cb and progress_cb(m)
    game_name = clean_title(game_name)

    cached = _load_cached(f"cover_{serial}")
    if cached:
        _log(f"Loaded cover from cache: {serial}")
        return cached

    steps = [
        (f"Searching xlenore covers for {serial}…",
         lambda: _try_xlenore(serial),
         "xlenore GitHub"),
        (f"Searching libretro boxarts for '{game_name}'…",
         lambda: _try_libretro_cover(game_name),
         "libretro") if game_name else None,
        (f"Querying TheGamesDB for '{game_name}'…",
         lambda: _try_tgdb_cover(game_name),
         "TheGamesDB") if game_name and _tgdb_api_key() else None,
        (f"Querying Sony TMDB for {serial}…",
         lambda: _try_sce_tmdb(serial),
         "Sony TMDB"),
        (f"Searching DuckDuckGo: '{game_name} PS1 cover art'…",
         lambda: _try_ddg_image(f"{game_name} PS1 PSX cover art"),
         "DuckDuckGo") if game_name else None,
    ]

    for step in steps:
        if step is None:
            continue
        msg, fn, source = step
        _log(msg)
        try:
            img = fn()
        except Exception:
            img = None
        if img:
            _log(f"Cover found via {source}.")
            _save_cached(f"cover_{serial}", img)
            return img

    _log("No cover art found; using placeholder.")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE PROCESSING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _fit_cover(img: Image.Image, target: tuple) -> Image.Image:
    """Fit preserving aspect ratio, letterbox with black."""
    img    = img.convert("RGBA")
    tw, th = target
    img.thumbnail((tw, th), Image.LANCZOS)
    canvas = Image.new("RGBA", target, (0, 0, 0, 255))
    canvas.paste(img, ((tw - img.width) // 2, (th - img.height) // 2), img)
    return canvas


def _fill_cover(img: Image.Image, target: tuple) -> Image.Image:
    """Scale-to-fill, centre-crop (no black bars)."""
    img    = img.convert("RGBA")
    tw, th = target
    scale  = max(tw / img.width, th / img.height)
    nw, nh = int(img.width * scale), int(img.height * scale)
    img    = img.resize((nw, nh), Image.LANCZOS)
    ox, oy = (nw - tw) // 2, (nh - th) // 2
    return img.crop((ox, oy, ox + tw, oy + th))


def _best_font(size: int) -> ImageFont.FreeTypeFont:
    """Try common fonts; fall back to PIL default."""
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf",
                 "FreeSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str,
               font, max_width: int) -> List[str]:
    """Simple word-wrap returning list of lines that fit max_width."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_width and cur:
            cur = test
        elif draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_text_shadow(draw: ImageDraw.ImageDraw, xy: tuple, text: str,
                      font, fill, shadow=(0, 0, 0, 180), offset=1):
    draw.text((xy[0] + offset, xy[1] + offset), text, font=font, fill=shadow)
    draw.text(xy, text, font=font, fill=fill)


def _vignette(img: Image.Image, strength: int = 140) -> Image.Image:
    """Apply an elliptical dark vignette to an RGBA/RGB image."""
    img    = img.convert("RGBA")
    w, h   = img.size
    vig    = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dv     = ImageDraw.Draw(vig)
    steps  = 60
    for i in range(steps):
        alpha = int((i / steps) ** 1.5 * strength)
        margin = i * 2
        dv.rectangle([margin, margin, w - margin, h - margin],
                     outline=(0, 0, 0, alpha))
    return Image.alpha_composite(img, vig)


def _gradient_bg(size: tuple, top=(10, 10, 30), bottom=(30, 10, 50)) -> Image.Image:
    """Create a smooth vertical gradient background."""
    w, h = size
    img  = Image.new("RGB", (w, h))
    pix  = img.load()
    for y in range(h):
        t = y / h
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            pix[x, y] = (r, g, b)
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# PSP IMAGE GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_placeholder(title: str, size: tuple) -> Image.Image:
    """Styled gradient placeholder with game title."""
    w, h   = size
    bg     = _gradient_bg(size, (26, 26, 46), (46, 16, 60))
    img    = bg.convert("RGBA")
    draw   = ImageDraw.Draw(img)
    # Border glow
    for i, alpha in [(3, 60), (2, 120), (1, 200)]:
        draw.rectangle([i, i, w - i, h - i],
                       outline=(233, 69, 96, alpha))
    font = _best_font(max(9, min(18, w // 12)))
    max_c = max(8, w // 8)
    disp  = title[:max_c - 1] + "…" if len(title) > max_c else title
    bbox  = draw.textbbox((0, 0), disp, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    _draw_text_shadow(draw, ((w - tw) // 2, (h - th) // 2),
                      disp, font, fill=(233, 69, 96, 240))
    draw.text((4, h - 13), "PS1", font=_best_font(8), fill=(160, 160, 192, 180))
    return img


def make_icon0(cover: Optional[Image.Image], game_name: str) -> Image.Image:
    """
    ICON0.PNG – 144×80.
    Tries smart-crop to remove black bars before resizing.
    """
    if cover:
        c = cover.convert("RGB")
        # Auto-crop near-black borders (common on scanned covers)
        try:
            bg   = Image.new("RGB", c.size, (8, 8, 8))
            diff = ImageOps.autocontrast(
                Image.eval(c, lambda px: abs(px - 8)))
            bbox = diff.getbbox()
            if bbox and (bbox[2] - bbox[0]) > 40 and (bbox[3] - bbox[1]) > 40:
                c = c.crop(bbox)
        except Exception:
            pass
        return _fit_cover(c.convert("RGBA"), ICON0_SIZE).convert("RGB")
    return _make_placeholder(game_name, ICON0_SIZE).convert("RGB")


def make_pic1(cover: Optional[Image.Image],
              screenshot: Optional[Image.Image],
              game_name: str) -> Image.Image:
    """
    PIC1.PNG – 480×272 XMB background.
    Uses screenshot if available (more atmospheric than blurred cover).
    Falls back to cover → blur/darken.
    """
    src = screenshot or cover
    if src:
        base = _fill_cover(src, PIC1_SIZE)
        # Gaussian blur
        base = base.filter(ImageFilter.GaussianBlur(radius=5))
        # Darken
        base = ImageEnhance.Brightness(base).enhance(0.50)
        # Slight colour saturation boost so it looks less grey
        base = ImageEnhance.Color(base).enhance(1.3)
        return base.convert("RGB")
    return _make_placeholder(game_name, PIC1_SIZE).convert("RGB")


def make_pic0(cover: Optional[Image.Image],
              game_name: str, serial: str) -> Image.Image:
    """
    PIC0.PNG – 480×272 transparent XMB overlay.
    Left panel: game title + serial + "PlayStation" badge.
    Right panel: crisp fitted cover thumbnail.
    Alpha background so PIC1 shows through.
    """
    canvas = Image.new("RGBA", PIC0_SIZE, (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)
    w, h   = PIC0_SIZE

    # ── right: cover thumbnail (220×200, centred vertically) ─────────────────
    if cover:
        thumb = _fit_cover(cover, (216, 196)).convert("RGBA")
        tx    = w - 224
        ty    = (h - 196) // 2
        # Rounded shadow beneath thumbnail
        shadow = Image.new("RGBA", (220, 200), (0, 0, 0, 0))
        sd     = ImageDraw.Draw(shadow)
        sd.rounded_rectangle([3, 3, 219, 199], radius=8, fill=(0, 0, 0, 100))
        canvas.paste(shadow, (tx - 2, ty + 2), shadow)
        canvas.paste(thumb, (tx, ty), thumb)

    # ── left: translucent panel ───────────────────────────────────────────────
    panel = Image.new("RGBA", (232, h), (0, 0, 0, 0))
    pd    = ImageDraw.Draw(panel)
    pd.rounded_rectangle([0, 20, 225, h - 20], radius=12,
                         fill=(10, 10, 30, 140))
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", PIC0_SIZE, 0))
    canvas.paste(panel, (0, 0), panel)
    draw = ImageDraw.Draw(canvas)

    # "PlayStation" top badge
    badge_font = _best_font(10)
    draw.rounded_rectangle([12, 26, 110, 42], radius=4, fill=(233, 69, 96, 200))
    draw.text((16, 28), "PlayStation", font=badge_font, fill=(255, 255, 255, 240))

    # Game title (word-wrapped, up to 3 lines)
    title_font = _best_font(17)
    lines      = _wrap_text(draw, game_name, title_font, 210)
    y          = 54
    for line in lines[:3]:
        _draw_text_shadow(draw, (14, y), line, title_font,
                          fill=(255, 255, 255, 230))
        y += 22

    # Serial
    serial_font = _best_font(10)
    draw.text((14, y + 8), serial, font=serial_font,
              fill=(190, 190, 230, 180))

    # Decorative bottom line
    draw.line([(12, h - 30), (218, h - 30)], fill=(233, 69, 96, 120), width=1)
    draw.text((14, h - 26), "PS1 → PSP", font=_best_font(9),
              fill=(150, 150, 200, 160))

    return canvas


def make_boot(cover: Optional[Image.Image],
              screenshot: Optional[Image.Image],
              game_name: str) -> Image.Image:
    """
    BOOT.PNG – 480×272 boot splash.
    Full-bleed cover or screenshot, heavy vignette, title text at bottom.
    """
    src = cover or screenshot
    if src:
        base = _fill_cover(src, BOOT_SIZE).convert("RGBA")
        base = _vignette(base, strength=160)
    else:
        base = _make_placeholder(game_name, BOOT_SIZE).convert("RGBA")

    draw = ImageDraw.Draw(base)
    w, h = BOOT_SIZE

    # Gradient overlay at bottom for readability
    overlay = Image.new("RGBA", (w, 60), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(60):
        alpha = int(y / 60 * 180)
        od.line([(0, y), (w, y)], fill=(0, 0, 10, alpha))
    base.paste(overlay, (0, h - 60), overlay)
    draw = ImageDraw.Draw(base)

    # Title at bottom
    font = _best_font(16)
    lines = _wrap_text(draw, game_name, font, w - 20)
    y = h - 20 - len(lines[:2]) * 20
    for line in lines[:2]:
        bbox = draw.textbbox((0, 0), line, font=font)
        tx   = (w - (bbox[2] - bbox[0])) // 2
        _draw_text_shadow(draw, (tx, y), line, font,
                          fill=(255, 255, 255, 230), shadow=(0, 0, 0, 200))
        y += 20

    return base.convert("RGB")


# ═══════════════════════════════════════════════════════════════════════════════
# ICON1 – animated icon frames (GIF-like multi-frame PNG sequence)
# ═══════════════════════════════════════════════════════════════════════════════

def make_icon1_frames(cover: Optional[Image.Image],
                      game_name: str,
                      n_frames: int = 16) -> List[Image.Image]:
    """
    Generate ICON1 animation frames (144×80 each).
    Creates a simple Ken-Burns pan/zoom effect on the cover art.
    PSP ICON1.PMF requires a real MPEG4/PMF encoder; we output the frames
    so the GUI can display a preview and optionally encode with ffmpeg.

    Returns list of *n_frames* PIL Images (RGB, 144×80).
    """
    frames = []
    src    = cover or _make_placeholder(game_name, (288, 160))
    src    = src.convert("RGB")
    iw, ih = ICON0_SIZE  # 144×80

    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)  # 0.0 → 1.0

        # Zoom from 1.0× to 1.15× while panning slightly right→left
        scale  = 1.0 + 0.15 * t
        nw     = int(src.width * scale)
        nh     = int(src.height * scale)
        scaled = src.resize((max(nw, iw), max(nh, ih)), Image.LANCZOS)

        # Pan offset: start centre, drift left by up to 8 px
        max_ox = scaled.width  - iw
        max_oy = scaled.height - ih
        ox     = int(max_ox * 0.5 - 4 * t)
        oy     = int(max_oy * 0.5)
        ox     = max(0, min(ox, max_ox))
        oy     = max(0, min(oy, max_oy))

        frame  = scaled.crop((ox, oy, ox + iw, oy + ih))
        frames.append(frame)

    return frames


def save_icon1_gif(frames: List[Image.Image], dest_path: str,
                   duration_ms: int = 80):
    """
    Save animation frames as an animated GIF at dest_path.
    PSP doesn't play GIFs natively, but useful for GUI preview and
    as input to ffmpeg → PMF conversion if ffmpeg is available.
    """
    if not frames:
        return
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    frames[0].save(
        dest_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=duration_ms,
        optimize=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ArtworkBundle + build_artwork_bundle()
# ═══════════════════════════════════════════════════════════════════════════════

class ArtworkBundle:
    """All PSP image assets plus raw source images."""
    def __init__(self):
        self.cover:      Optional[Image.Image]       = None
        self.screenshot: Optional[Image.Image]       = None
        self.icon0:      Optional[Image.Image]       = None  # 144×80 RGB
        self.pic0:       Optional[Image.Image]       = None  # 480×272 RGBA
        self.pic1:       Optional[Image.Image]       = None  # 480×272 RGB
        self.boot:       Optional[Image.Image]       = None  # 480×272 RGB
        self.icon1_frames: List[Image.Image]         = []    # animation frames

    def save_all(self, dest_dir: str, prefix: str = "") -> dict:
        """Save all non-None images to dest_dir.  Returns {slot: path}."""
        os.makedirs(dest_dir, exist_ok=True)
        saved = {}
        for name, img in [("ICON0", self.icon0), ("PIC0", self.pic0),
                           ("PIC1", self.pic1),  ("BOOT", self.boot)]:
            if img is None:
                continue
            fname = f"{prefix}{name}.PNG" if prefix else f"{name}.PNG"
            path  = os.path.join(dest_dir, fname)
            img.save(path, "PNG")
            saved[name] = path

        # Save animated GIF preview of ICON1 if frames exist
        if self.icon1_frames:
            gif_path = os.path.join(dest_dir,
                                    f"{prefix}ICON1_preview.gif" if prefix
                                    else "ICON1_preview.gif")
            save_icon1_gif(self.icon1_frames, gif_path)
            saved["ICON1_preview"] = gif_path

        return saved


def build_artwork_bundle(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    custom_cover_path:      Optional[str] = None,
    custom_screenshot_path: Optional[str] = None,
    chosen_cover:           Optional[ImageCandidate] = None,
    chosen_screenshot:      Optional[ImageCandidate] = None,
    fetch_screenshot:       bool = True,
    animate_icon:           bool = True,
) -> ArtworkBundle:
    """
    Fetch cover + screenshot (if available), generate all PSP image assets.

    Args:
        serial:                  normalised serial e.g. SLUS01234
        game_name:               display title for search queries
        progress_cb:             optional log callback(str)
        custom_cover_path:       local file path override for cover
        custom_screenshot_path:  local file path override for screenshot
        chosen_cover:            pre-selected ImageCandidate from search_cover_candidates()
        chosen_screenshot:       pre-selected ImageCandidate for background
        fetch_screenshot:        whether to search for a screenshot
        animate_icon:            whether to generate ICON1 animation frames
    """
    def _log(m): progress_cb and progress_cb(m)
    bundle = ArtworkBundle()

    # ── Cover ─────────────────────────────────────────────────────────────────
    if custom_cover_path and os.path.isfile(custom_cover_path):
        try:
            bundle.cover = Image.open(custom_cover_path).convert("RGBA")
            _log(f"Using custom cover: {os.path.basename(custom_cover_path)}")
        except Exception as e:
            _log(f"Could not load custom cover: {e}")

    if bundle.cover is None and chosen_cover is not None:
        bundle.cover = resolve_candidate(chosen_cover)
        if bundle.cover:
            _log(f"Using selected cover: {chosen_cover.display_label()}")

    if bundle.cover is None:
        bundle.cover = fetch_cover_art(serial, game_name, progress_cb)

    # ── Screenshot ────────────────────────────────────────────────────────────
    if custom_screenshot_path and os.path.isfile(custom_screenshot_path):
        try:
            bundle.screenshot = Image.open(custom_screenshot_path).convert("RGBA")
            _log(f"Using custom screenshot: {os.path.basename(custom_screenshot_path)}")
        except Exception as e:
            _log(f"Could not load custom screenshot: {e}")

    if bundle.screenshot is None and chosen_screenshot is not None:
        bundle.screenshot = resolve_candidate(chosen_screenshot)
        if bundle.screenshot:
            _log(f"Using selected screenshot: {chosen_screenshot.display_label()}")

    if bundle.screenshot is None and fetch_screenshot:
        bundle.screenshot = _fetch_screenshot_all(serial, game_name, progress_cb)

    # ── Generate PSP assets ───────────────────────────────────────────────────
    _log("Generating PSP image assets…")

    bundle.icon0 = make_icon0(bundle.cover, game_name)
    bundle.pic1  = make_pic1(bundle.cover, bundle.screenshot, game_name)
    bundle.pic0  = make_pic0(bundle.cover, game_name, serial)
    bundle.boot  = make_boot(bundle.cover, bundle.screenshot, game_name)

    if animate_icon:
        bundle.icon1_frames = make_icon1_frames(bundle.cover, game_name)

    _log("Artwork bundle ready.")
    return bundle


def _fetch_screenshot_all(
    serial: str,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[Image.Image]:
    """Internal: try all screenshot sources."""
    return fetch_screenshot(serial, game_name, progress_cb)

