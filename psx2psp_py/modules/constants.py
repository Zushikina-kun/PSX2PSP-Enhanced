"""
constants.py – All fixed paths, sizes, and tool references for PSX2PSP Python.
"""
import os
import sys

# ── Root of the PSX2PSP installation (parent of psx2psp_py/) ─────────────────
# When running as a PyInstaller frozen EXE:
#   sys.frozen = True
#   sys.executable = path to the .exe  (e.g.  C:\Games\PSX2PSP\PSX2PSP_Enhanced.exe)
#   __file__       = inside _MEIPASS temp dir (wrong for data files!)
# When running from source:
#   __file__ = .../psx2psp_py/modules/constants.py  → two levels up = project root
#
# We always want the directory containing the EXE (or the project root when
# running from source) so that at3tool.exe, Files/gameInfo.db etc. are found.

if getattr(sys, "frozen", False):
    # Frozen EXE: root = directory of the .exe itself
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    ROOT_DIR  = _EXE_DIR
    PY_DIR    = _EXE_DIR          # cache/output sit next to the exe
else:
    # Source: root = two levels above this file (…/psx2psp_py/modules/constants.py)
    ROOT_DIR  = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    PY_DIR    = os.path.join(ROOT_DIR, "psx2psp_py")

CACHE_DIR  = os.path.join(PY_DIR, "cache")
OUTPUT_DIR = os.path.join(PY_DIR, "output")
ASSETS_DIR = os.path.join(PY_DIR, "assets")

# ── Tool executables ──────────────────────────────────────────────────────────
AT3TOOL_EXE    = os.path.join(ROOT_DIR, "at3tool.exe")
LAME_EXE       = os.path.join(ROOT_DIR, "lame.exe")
POCKETISO_EXE  = os.path.join(ROOT_DIR, "PocketISO.exe")
POPSTATION_DLL = os.path.join(ROOT_DIR, "Files", "popstation.dll")
BASE_PBP       = os.path.join(ROOT_DIR, "Files", "BASE.PBP")
DATA_PSP       = os.path.join(ROOT_DIR, "Files", "DATA.PSP")
GAMEINFO_DB    = os.path.join(ROOT_DIR, "Files", "gameInfo.db")
PATCHES_INI    = os.path.join(ROOT_DIR, "Files", "patches.ini")
SETTINGS_INI   = os.path.join(ROOT_DIR, "Files", "settings.ini")
NO_ICON_PNG    = os.path.join(ROOT_DIR, "Files", "no_icon0.png")

# ── PSP image spec ────────────────────────────────────────────────────────────
ICON0_SIZE   = (144, 80)    # ICON0.PNG   – animated icon (first frame)
PIC0_SIZE    = (480, 272)   # PIC0.PNG    – overlay (shown on XMB)
PIC1_SIZE    = (480, 272)   # PIC1.PNG    – background
BOOT_SIZE    = (480, 272)   # BOOT.PNG    – boot splash

# ── AT3 audio encoding ────────────────────────────────────────────────────────
AT3_BITRATE  = 132          # kbps (66 or 132)

# ── Artwork / BGM online sources ──────────────────────────────────────────────

# Cover art ───────────────────────────────────────────────────────────────────
COVER_URL_XLENORE  = "https://raw.githubusercontent.com/xlenore/psx-covers/main/covers/default/{serial}.jpg"
COVER_URL_XLENORE2 = "https://raw.githubusercontent.com/xlenore/psx-covers/main/covers/{serial}.jpg"

# Sony SCE TMDB (public, no auth for metadata; icons may require regional access)
SCE_TMDB_META   = "https://sce.tmdb.api.playstation.com/v1/gameTitles/{serial}/MASTER?age=99&country=US&language=en"

# Libretro thumbnails (boxart + screenshots + title screens) – free/open
LIBRETRO_THUMB  = "https://thumbnails.libretro.com/Sony%20-%20PlayStation/Named_Boxarts/{name}.png"
LIBRETRO_SNAP   = "https://thumbnails.libretro.com/Sony%20-%20PlayStation/Named_Snaps/{name}.png"
LIBRETRO_TITLE  = "https://thumbnails.libretro.com/Sony%20-%20PlayStation/Named_Titles/{name}.png"

# TheGamesDB – free public API (optional key via env TGDB_API_KEY)
TGDB_SEARCH     = "https://api.thegamesdb.net/v1/Games/ByGameName?name={name}&filter[platform]=10&apikey={key}&fields=game_title,overview&include=boxart"
TGDB_IMAGES     = "https://api.thegamesdb.net/v1/Games/Images?games_id={gid}&apikey={key}"
TGDB_IMG_BASE   = "https://cdn.thegamesdb.net/images/original/"

# PSX Data Center (free, no auth) – per-serial game info & screenshots
PSXDC_GAME      = "https://psxdatacenter.com/games/U/{id}.html"

# BGM sources ─────────────────────────────────────────────────────────────────
# KH Insider (khinsider) – largest PSX soundtrack archive
KHI_BASE        = "https://downloads.khinsider.com"
KHI_SEARCH      = "https://downloads.khinsider.com/search?search={query}"
KHI_ALBUM       = "https://downloads.khinsider.com/game-soundtracks/album/{slug}"

# Internet Archive advanced search API
ARCHIVE_SEARCH  = "https://archive.org/advancedsearch.php?q={query}&fl[]=identifier&fl[]=title&output=json&rows=8&mediatype=audio"
ARCHIVE_META    = "https://archive.org/metadata/{identifier}"
ARCHIVE_DOWNLOAD= "https://archive.org/download/{identifier}/{filename}"

# ── Compression levels (0 = no compression, 9 = max) ─────────────────────────
DEFAULT_COMPRESS = 9

# ── App UI colours ────────────────────────────────────────────────────────────
CLR_BG      = "#1a1a2e"
CLR_PANEL   = "#16213e"
CLR_ACCENT  = "#0f3460"
CLR_HILIGHT = "#e94560"
CLR_FG      = "#eaeaea"
CLR_FG2     = "#a0a0c0"
CLR_GREEN   = "#00c896"
CLR_ORANGE  = "#ffa040"
CLR_RED     = "#ff4060"

FONT_TITLE  = ("Segoe UI", 14, "bold")
FONT_LABEL  = ("Segoe UI", 9)
FONT_SMALL  = ("Segoe UI", 8)
FONT_MONO   = ("Consolas", 8)
