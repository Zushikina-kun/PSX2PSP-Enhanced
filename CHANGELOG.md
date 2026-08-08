# Changelog

All notable changes to PSX2PSP Enhanced (Python Edition) are recorded here.

---

## [Unreleased] — Python Edition v1.0.0

### Added — Core Python rewrite
- Full Python 3.9+ rewrite of the entire frontend and asset pipeline
- `psx2psp_py/psx2psp.py` — CLI + GUI unified launcher
- `PSX2PSP_Enhanced.bat` — Windows one-click launcher with dependency hints

### Added — Game DB (`modules/game_db.py`)
- Auto-detection of PS1 serial from raw BIN/ISO/CUE filesystem (Mode1 + Mode2/Form1)
- CUE sheet parser — resolves multi-track and single-track images
- 10,138-entry `gameInfo.db` lookup with all serial variant normalisation
  (`SLUS_000.67` → `SLUS-00067` → `SLUS00067` all resolve to the same entry)
- `search_games()` for the in-app game database search dialog

### Added — Cover Art (`modules/artwork.py`)
- 5 cover art sources: xlenore GitHub, Libretro boxarts, TheGamesDB, Sony TMDB, DuckDuckGo
- 4 screenshot sources: Libretro snaps/titles, TheGamesDB, PSX Data Center
- `search_cover_candidates()` and `search_screenshot_candidates()` — return all
  candidates so the user can pick from a thumbnail grid
- `ImageCandidate` dataclass with lazy thumbnail loading
- Improved image generators:
  - `make_icon0` — auto-crops near-black borders before resizing to 144×80
  - `make_pic0` — translucent left panel with PlayStation badge, word-wrapped title,
    serial, footer; cover thumbnail on right with drop-shadow
  - `make_pic1` — uses screenshot when available (blurred, darkened, colour-boosted)
  - `make_boot` — full-bleed with elliptical vignette + gradient overlay + title text
- `make_icon1_frames()` — 16-frame Ken-Burns pan/zoom animation sequence
- `save_icon1_gif()` — saves animated GIF preview for ICON1

### Added — BGM Pipeline (`modules/bgm.py`)
- 3 BGM sources: KH Insider, Internet Archive, YouTube (yt-dlp)
- `BgmCandidate` dataclass returned by all search functions
- `_khi_slug_candidates()` — generates 34+ slug variants per game title including
  year suffixes (1994–2001), sound-track hyphenation, psx/ps1 variants
- `_khi_search()` — full HTML scrape of KH Insider search page extracting real hrefs
- `_khi_get_tracks()` — per-album track listing for fine-grained track selection
- `_archive_search()` — Internet Archive search API with item file listing
- `search_bgm()` — aggregates all sources into one candidate list
- `download_bgm_candidate()` — downloads and converts a specific candidate
- `get_khi_tracks()` — public API for track picker in GUI
- `clean_title()` — strips `[NTSC-J]`, `[Disc1of2]`, `(English v1.3)` etc. from
  DB titles before all search queries (applied universally)
- AT3 pipeline: MP3 → WAV via `lame --decode`, WAV → AT3 via `at3tool.exe`
- `shell=True` + `PIPE` subprocess pattern fixes at3tool.exe hang on Windows
- `search_and_get_bgm()` with optional `pick_cb` for GUI integration

### Added — Converter (`modules/converter.py`)
- ctypes wrapper for `popstation.dll` (`ConvertIsoInfo` struct, `ConvertCallback`)
- `normalise_to_iso()` — single-track CUE resolved directly to BIN (no PocketISO)
  Multi-track CUEs fall back to PocketISO with `shell=True` subprocess fix
- `_find_python32()` — detects 32-bit Python install for bridge fallback
- `_run_via_bridge()` — spawns `popstation_bridge.py` under 32-bit Python when
  64-bit Python cannot load the 32-bit DLL
- Clear error message with install instructions when 32-bit Python is absent

### Added — Batch Runner (`modules/batch.py`)
- `GameSpec` dataclass with all per-game options including `bgm_sources` list
- `Pipeline` — full sequential pipeline: serial → DB → normalise → artwork → BGM → convert
- `BatchRunner` — sequential batch with per-job callbacks, cancel support

### Added — GUI (`modules/gui.py`)
- Dark-themed tkinter/ttk interface, no external theme libraries
- **PreviewPanel** — 5-slot tabbed preview (ICON0/PIC0/PIC1/BOOT/ICON1-anim)
  with live animated icon playback via `after()` loop
- **BgmPickDialog** — album list (left) + track list (right, loaded on demand for KHI)
- **ImagePickDialog** — lazy-loading thumbnail grid for cover/screenshot selection
- **SearchDialog** — in-app game DB search with live filtering
- **SingleGameTab** — disc picker, auto-detect, per-slot asset overrides incl. screenshot
- **BatchTab** — queue with treeview, auto-fill, per-game status updates
- **SettingsTab** — BGM source checkboxes, TheGamesDB API key field,
  screenshot/animation toggles, cache management
- **AboutTab** — full dependency status, artwork and BGM source list
- Settings persistence via `psx2psp_py.ini`

### Added — Docs & Tooling
- `README.md` — full feature overview, setup, usage, project structure, known limitations
- `CHANGELOG.md` — this file
- `.gitignore` — ignores cache, output, pyc files, test artefacts
- `test_smoke.py` — 24-check regression suite (all pass except ffmpeg missing)
- `test_at3tool.py`, `test_artwork.py`, `test_bgm.py` — focused developer utilities
- `popstation_bridge.py` — standalone 32-bit Python bridge for popstation.dll

### Fixed
- at3tool.exe hangs when called via `subprocess.run` with `DEVNULL` from Python —
  root cause: 32-bit legacy console app blocks when stdout is NUL; fixed by using
  `shell=True` + `stdout=PIPE`
- CUE files with a single track no longer invoke PocketISO (was hanging on large files)
- `orient="h"` in ttk.Separator — corrected to `orient="horizontal"` (6 occurrences)
- `os.startfile()` replaced with cross-platform `_open_folder()` helper

---

## [Legacy] — PSX2PSP by KingSquitter (original)

The original `PSX2PSP.exe` and associated `.exe`/`.dll` tools remain in the root
directory and continue to work independently. The Python edition wraps the same
`popstation.dll` engine but adds the automated asset pipeline on top.
