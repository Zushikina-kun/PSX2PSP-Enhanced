# PSX2PSP Enhanced — Python Edition

[![Release](https://img.shields.io/github/v/release/Zushikina-kun/PSX2PSP-Enhanced?label=Download&color=brightgreen)](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/latest)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

A comprehensive PS1 → PSP (EBOOT.PBP) conversion tool with automatic cover art search, screenshot search, BGM/soundtrack download, and ATRAC3 audio conversion.

Built on top of the original PSX2PSP by KingSquitter, with a full Python rewrite of the frontend, asset pipeline, and search/download systems.

---

## Download

### Pre-built Windows EXE (no Python needed)

| File | Size | SHA256 |
|------|------|--------|
| [PSX2PSP_Enhanced_v1.1.3_Windows_x64.zip](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/latest) | ~30 MB | *(see release page)* |
| [PSX2PSP_Enhanced_v1.1.2_Windows_x64.zip](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/download/v1.1.2/PSX2PSP_Enhanced_v1.1.2_Windows_x64.zip) | 29.9 MB | `FF10F382...B38B20` |
| [PSX2PSP_Enhanced_v1.0.0_Windows_x64.zip](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/download/v1.0.0/PSX2PSP_Enhanced_v1.0.0_Windows_x64.zip) | 29.1 MB | `D1CA28E5...350CB47` |

1. Download the latest zip from the [Releases page](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases)
2. Extract it anywhere — keep all files in the same folder
3. Double-click **`PSX2PSP_Enhanced.exe`** to launch the GUI

> **Note:** `popstation.dll` (the PS1→PBP engine) is 32-bit. For PBP conversion you
> need 32-bit Python — see the [popstation.dll requirement](#popstationdll--32-bit-requirement) section below.

---

## Features

### Conversion
- Converts PS1 disc images (`.bin`, `.iso`, `.img`, `.cue`) to PSP `EBOOT.PBP`
- Multi-disc support (up to 4 discs in one PBP)
- Serial auto-detection from raw disc image filesystem
- 10,138-entry `gameInfo.db` lookup for title, save folder, region, video format
- CUE sheet parsing — single-track CUEs resolve directly to BIN (no PocketISO needed)
- PAL → NTSC patch support via `patches.ini`
- Compression levels 0–9 (zlib)

### Patches / Mods / Translations — 3 search sources

| # | Source | Coverage |
|---|--------|---------|
| 1 | **romhacking.net** | Translations + Hacks (platform = PlayStation) |
| 2 | **Archive.org** | Community patch collections (`.ips`/`.bps`/`.xdelta`) |
| 3 | **PSX-Place** | Tag pages: english-patch, ps1-patches, translation |

Supported patch formats: **IPS** · **BPS** (Beat, CRC32 verified) · **xdelta3** · **ZIP** (auto-extracts)

Use the `🩹 Patches / Mods` button in the Single Game tab to search, preview, and apply patches before conversion. Local patch files (`.ips`/`.bps`/`.xdelta`/`.zip`) can be added directly.
| # | Source | Key required |
|---|--------|-------------|
| 1 | **xlenore/psx-covers** (GitHub raw, serial-based) | No |
| 2 | **Libretro Named_Boxarts** (game-name based) | No |
| 3 | **TheGamesDB v2 API** | Optional (`TGDB_API_KEY` env var) |
| 4 | **Sony SCE TMDB API** (serial-based) | No |
| 5 | **DuckDuckGo image search** (fallback) | No |

### Screenshots — 4 sources
Used for PIC1 background and BOOT splash images:
- Libretro Named_Snaps (in-game screenshots)
- Libretro Named_Titles (title screens)
- TheGamesDB screenshots (with API key)
- PSX Data Center (HTML scrape)

### BGM / Soundtrack — 3 sources
| # | Source | How |
|---|--------|-----|
| 1 | **KH Insider** (downloads.khinsider.com) | Full HTML search + album/track picker |
| 2 | **Internet Archive** (archive.org) | Search API + item file listing |
| 3 | **YouTube** via yt-dlp | ytsearch fallback |

All sources feed into: **WAV** (via lame/ffmpeg) → **AT3** (via at3tool.exe at 132 kbps).

### PSP Image Assets Generated
| File | Size | Description |
|------|------|-------------|
| `ICON0.PNG` | 144×80 | Cover thumbnail, auto-crops black borders |
| `PIC0.PNG` | 480×272 | XMB overlay — cover + title panel, transparent background |
| `PIC1.PNG` | 480×272 | XMB background — screenshot or cover, blurred + darkened |
| `BOOT.PNG` | 480×272 | Boot splash — full-bleed with vignette + title text |
| `ICON1_preview.gif` | 144×80×16f | Animated icon preview (Ken-Burns pan/zoom) |

### GUI
- Dark-themed tkinter UI (no external theme libraries)
- **Single Game** tab: disc picker, auto-detect, per-slot image preview with animation
- **Batch** tab: queue multiple games, auto-fill all, convert all
- **Settings** tab: per-source BGM toggles, TheGamesDB API key, screenshot/animation toggles
- **About** tab: dependency status, source list
- **Image picker dialog**: thumbnail grid — browse all found covers/screenshots and pick one
- **BGM picker dialog**: album list + track list — choose exact album and track
- All preview slots (ICON0 / PIC0 / PIC1 / BOOT / ICON1 anim) switchable with tab bar

---

## Requirements

### Python
- Python **3.9+** (3.14 recommended)
- **64-bit Python** for the GUI and all features except PBP conversion

### Python packages
```
pip install pillow requests yt-dlp tqdm mutagen
```

| Package | Version tested | Purpose |
|---------|---------------|---------|
| Pillow | 12.3.0 | Image processing |
| requests | 2.34.2 | HTTP (used internally) |
| yt-dlp | 2026.7.4 | YouTube BGM download |
| tqdm | 4.70.0 | Progress bars (CLI) |
| mutagen | 1.48.1 | Audio metadata |

### Bundled tools (already in the folder)
| Tool | Purpose |
|------|---------|
| `at3tool.exe` | WAV → ATRAC3 encoder (Sony, 32-bit) |
| `lame.exe` | MP3 ↔ WAV decoder/encoder |
| `PocketISO.exe` | Multi-track CUE merger (used for complex images) |
| `PSX2PSP.exe` | Original GUI (still works independently) |
| `Files/popstation.dll` | PBP conversion engine (32-bit DLL) |
| `Files/BASE.PBP` | PSP base firmware stub |
| `Files/gameInfo.db` | 10,138-entry game metadata database |

### popstation.dll — 32-bit requirement
`popstation.dll` is a **32-bit Windows DLL**. If you're running 64-bit Python (the default), you need to also install **32-bit Python** for PBP conversion:

1. Download **Python 3.x (32-bit)** from https://www.python.org/downloads/ — choose *Windows installer (32-bit)*
2. Set the environment variable before launching:
   ```
   set PYTHON32=C:\Python312-32\python.exe
   ```
3. The tool will automatically use the bridge script (`popstation_bridge.py`) for the actual DLL call.

Alternatively, just use the bundled **PSX2PSP.exe** for the conversion step after the Python tool has prepared all the assets.

### Optional
- **ffmpeg** — enables non-MP3 audio formats (OGG, FLAC, M4A, OPUS → AT3). Without ffmpeg, only MP3 and WAV inputs work for BGM.

---

## Setup

### Option A — Pre-built EXE (recommended)
Download and extract the [latest release](https://github.com/Zushikina-kun/PSX2PSP-Enhanced/releases/latest). Run `PSX2PSP_Enhanced.exe`.

### Option B — Run from source (requires Python 3.9+)

```bash
# 1. Clone or download
git clone https://github.com/Zushikina-kun/PSX2PSP-Enhanced.git
cd PSX2PSP-Enhanced

# 2. Install Python dependencies
pip install pillow requests yt-dlp tqdm mutagen

# 3. Launch (Windows)
PSX2PSP_Enhanced.bat

# or directly:
python psx2psp_py/psx2psp.py
```

---

## Usage

### GUI mode
Double-click `PSX2PSP_Enhanced.bat` or run:
```
python psx2psp_py/psx2psp.py
```

**Single Game workflow:**
1. Click **➕ Add Disc** and select your `.cue` / `.bin` / `.iso`
2. Click **🔍 Auto-Detect** — fills in serial, title, region from the DB
3. Click **🎨 Fetch Artwork** — searches all sources and shows a thumbnail picker
4. Click **🎵 Fetch BGM** — searches KH Insider / Archive.org / YouTube, shows an album+track picker
5. Click **▶ CONVERT**

### CLI mode
```
python psx2psp_py/psx2psp.py --cli "Game.cue" [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--out DIR` | `psx2psp_py/output` | Output directory |
| `--title "TITLE"` | auto | Override game title |
| `--no-artwork` | — | Skip artwork fetch |
| `--no-bgm` | — | Skip BGM fetch |
| `--compress N` | 9 | Compression level 0–9 |
| `--patches` | — | Apply PAL→NTSC patches |

### TheGamesDB API key (optional, improves cover/screenshot quality)
1. Register at https://www.thegamesdb.net/
2. Get your API key from your profile
3. Either set the environment variable: `set TGDB_API_KEY=your_key_here`
4. Or enter it in Settings → Artwork → TheGamesDB API key

---

## Project Structure

```
PSX2PSP-Enhanced/
├── PSX2PSP_Enhanced.bat        Windows launcher
├── PSX2PSP.exe                 Original PSX2PSP GUI
├── at3tool.exe                 ATRAC3 encoder
├── lame.exe                    MP3/WAV encoder
├── PocketISO.exe               Multi-track CUE converter
├── Files/
│   ├── popstation.dll          PBP conversion DLL
│   ├── BASE.PBP                Base firmware stub
│   ├── gameInfo.db             Game metadata (10,138 entries)
│   └── patches.ini             PAL→NTSC patches
└── psx2psp_py/
    ├── psx2psp.py              Main launcher (GUI + CLI)
    ├── popstation_bridge.py    32-bit Python bridge for popstation.dll
    ├── modules/
    │   ├── constants.py        Paths, sizes, colours, URLs
    │   ├── game_db.py          Serial detection + gameInfo.db lookup
    │   ├── artwork.py          Cover/screenshot search + PSP image generation
    │   ├── bgm.py              BGM search, download, AT3 conversion
    │   ├── converter.py        popstation.dll ctypes wrapper + CUE normaliser
    │   ├── batch.py            BatchRunner, Pipeline, GameSpec
    │   └── gui.py              Full tkinter GUI
    ├── test_smoke.py           Regression test suite
    ├── test_artwork.py         Artwork pipeline test
    ├── test_at3tool.py         AT3 conversion test
    └── test_bgm.py             BGM tool availability test
```

---

## Known Limitations

| Limitation | Workaround |
|-----------|-----------|
| `popstation.dll` is 32-bit; 64-bit Python can't load it directly | Install 32-bit Python and set `PYTHON32` env var |
| KH Insider may rate-limit or return 403 in some environments | Use Archive.org or YouTube sources instead |
| ffmpeg not bundled — non-MP3 BGM formats won't decode without it | Install ffmpeg from https://ffmpeg.org/download.html |
| ICON1.PMF (animated icon video) not encoded — preview GIF only | Convert preview GIF → PMF with ffmpeg + Sony PMF tools if needed |
| Screenshot fetch depends on Libretro/PSX Data Center availability | Supply a custom screenshot via the asset override field |

---

## Building from Source

Requires Python 3.9+, PyInstaller, and UPX for maximum compression.

```bash
# 1. Install build dependencies
pip install pyinstaller pillow requests yt-dlp tqdm mutagen

# 2. Download UPX (optional but recommended — shrinks EXE by ~40%)
#    https://github.com/upx/upx/releases/latest
#    Extract upx.exe and add its folder to PATH

# 3. Build (run from the project root)
python -m PyInstaller psx2psp_py/psx2psp_enhanced.spec ^
    --distpath dist --workpath build/pyinstaller --clean --noconfirm
```

Output: `dist/PSX2PSP_Enhanced.exe` (~28 MB single-file, UPX compressed)

Build environment used for official releases:
| Component | Version |
|-----------|---------|
| Python | 3.14.6 (64-bit) |
| PyInstaller | 6.21.0 |
| UPX | 5.2.0 |
| Platform | Windows 11 x64 |

---

## Credits

- **Original PSX2PSP** by KingSquitter
- **popstation.dll** — Sony Computer Entertainment Inc.
- **at3tool** — Sony Computer Entertainment Inc.
- **gameInfo.db** — PSX2PSP community
- **xlenore/psx-covers** — https://github.com/xlenore/psx-covers
- **Libretro thumbnails** — https://thumbnails.libretro.com
- **KH Insider** — https://downloads.khinsider.com
- Python edition by [Zushikina-kun](https://github.com/Zushikina-kun/PSX2PSP-Enhanced)

---

## License

The Python scripts in `psx2psp_py/` are released under the MIT License.
The bundled tools (`at3tool.exe`, `popstation.dll`, `PSX2PSP.exe`) retain their original licenses from Sony/KingSquitter.
