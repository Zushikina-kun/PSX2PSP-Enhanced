# Changelog

All notable changes to PSX2PSP Enhanced (Python Edition) are recorded here.

---

## [1.1.3] — In-App Audio Preview

### Added
- **In-app BGM player** — `pygame-ce` powered audio player embedded directly in
  `BgmPickDialog`. No external media player needed.
  - ▶ Play / ⏸ Pause / ⏹ Stop buttons
  - Progress bar with time display (current / total)
  - Volume slider (0–100 %)
  - Smooth seek bar — click anywhere on the progress track to jump
  - Auto-stops and resets when the dialog closes
- `AudioPlayer` class in `modules/bgm.py` — thin wrapper around `pygame.mixer`
  that handles init, load, play, pause/resume, stop, seek, volume, and position
  polling; gracefully no-ops when pygame is unavailable
- Preview download still caches the file; replaying doesn't re-download

### Changed
- `BgmPickDialog._play_preview()` now calls `AudioPlayer` instead of
  `os.startfile()` — playback is fully in-process
- `BgmPickDialog._cancel()` stops playback before destroying the window
- `requirements.txt` updated: `pygame` → `pygame-ce>=2.5.0` (pygame-ce supports
  Python 3.14; original pygame does not)

---

## [1.1.2] — BGM AT3 Fixes & Packaging Fix (updated zip)

### Fixed — AT3 encoding always failed in the pre-built EXE (packaging bug)
- **`msvcr71.dll` was missing** from every release package since v1.0.0.
  `at3tool.exe` requires this Visual C++ 2003 runtime DLL; without it the process
  exits with `0xC0000135` (STATUS_DLL_NOT_FOUND) and produces no output.
- **`Files/popstation.dll` was missing** from every release package. The conversion
  engine was never copied into the release folder, causing instant `popstation.dll
  not found` failure on every conversion attempt.
- Added `build_release.bat` — documented packaging script that lists every
  required file; prevents these omissions in future releases.
- Added `test_at3_frozen.py` — pre-release test that simulates the frozen-EXE
  environment and verifies at3tool + lame work from the release folder.

### Fixed — AT3 encoding from downloaded audio
- `_get_audio_ext()` — magic-byte detection (ID3/MPEG, fLaC, OggS, RIFF/WAVE,
  ftyp/M4A, WebM matroska header); no longer trusts file extension alone
- `_to_at3()` — routes each format to the correct decoder:
  - MP3  → `lame --decode` (no ffmpeg needed)
  - WAV  → resample or direct copy
  - OGG/FLAC/WebM/M4A → requires ffmpeg; shows clear error if absent
  - Removed silent "try source directly" fallback that passed WebM to at3tool
- `_ytdlp_download_candidate()` — without ffmpeg, format is restricted to
  `mp3/m4a`; WebM/Opus/OGG are explicitly rejected with a helpful message

### Added — BGM Picker UX
- Tracks auto-load immediately when an album is selected (no manual button)
- First KH Insider album is pre-selected and tracks auto-load on dialog open
- Double-click a track to preview it
- Preview button downloads track to temp and opens in system default player
- Non-KHI sources show an info row instead of empty track list
- Cancel cleans up temp preview files

---

## [1.1.1] — Hotfix: EXE close hang + frozen-EXE DB path

### Fixed
- **App could not be closed** — Background daemon threads (`done_event.wait(300)`
  in BGM/artwork/patch workers, yt-dlp downloads) kept the EXE process alive after
  the window closed. `App._on_close()` now calls `sys.exit(0)` after `destroy()`
  so all daemon threads terminate immediately. Running `BatchRunner` is cancelled first.
- **"Not found in gameInfo.db" in pre-built EXE** — `constants.py` computed
  `ROOT_DIR` relative to `__file__`, which in a PyInstaller frozen EXE points to
  the temporary `_MEIPASS` extract folder, not the folder containing the EXE.
  When `sys.frozen = True`, `ROOT_DIR` is now set from `sys.executable`'s
  directory so `Files/gameInfo.db`, `at3tool.exe`, etc. are found correctly.

---

## [1.1.0] — Patch / Mod / Translation System

### Added — `modules/patches.py` (new module)
- **IPS parser** — full spec: normal records, RLE records, truncation suffix
- **BPS (Beat) parser** — variable-length encoding, all 4 commands (SourceRead,
  TargetRead, SourceCopy, TargetCopy), CRC32 source + output verification
- **xdelta3 support** — subprocess call to `xdelta3.exe`; auto-discovers binary
  in `tools/xdelta3.exe` or system PATH
- **ZIP dispatch** — automatically extracts first `.ips`/`.bps`/`.xdelta` file
  from a ZIP archive and applies it
- `detect_patch_format()` — magic-byte + extension detection
- `patch_iso(iso, patch, out)` — unified entry point for all formats
- `PatchCandidate` dataclass — source, title, URL, patch_type, author, local_path
- **romhacking.net search** — scrapes Translations + Hacks pages for platform=6
  (PlayStation), extracts detail page links, resolves direct download URL
- **Archive.org search** — search API for `.ips`/`.bps`/`.xdelta` files in
  audio/software items, per-item file listing
- **PSX-Place search** — scrapes english-patch, ps1-patches, translation tag pages
- `search_patches()` — unified multi-source search returning all candidates
- `download_patch()` — download + cache by serial, auto-detect format
- `apply_patches_to_iso()` — apply a list of PatchCandidates sequentially to disc 1

### Added — GUI (`modules/gui.py`)
- **`PatchPickDialog`** — full patch picker with:
  - Source checkboxes (romhacking / archive / psxplace)
  - Live search bar + threaded search
  - Scrollable results list with source badge, format tag, title, author
  - `🔗` button to open romhacking/psxplace page in browser
  - Select All / None buttons
  - Local patch file browse + add (`.ips`/`.bps`/`.xdelta`/`.zip`)
  - Download & Apply Selected button (threaded download, then calls `result_cb`)
- **`SingleGameTab`** — new `🩹 Patches / Mods` button + status label
- **`SettingsTab`** — new "Patches / Mods / Translations" LabelFrame:
  - "Auto-search for patches" checkbox
  - Per-source checkboxes (romhacking.net / Archive.org / PSX-Place)
  - xdelta3 info note
- **`AboutTab`** — Patch Sources section listing all 3 sources + formats
- `_check_deps()` now checks xdelta3 availability

### Fixed — existing patch bugs
- `BatchTab._start_all()` — now propagates `apply_patches` and `auto_search_patches`
  from settings to each spec (was silently skipping patches in batch mode)
- `converter.py _run_via_bridge()` — JSON spec now includes `apply_patches`
  (bridge path was always patching with 0 patches regardless of setting)
- `popstation_bridge.py run()` — now calls `_build_patch_array()` and passes
  `patchCount`/`patchData` to the DLL when `apply_patches=True`

### Changed — `modules/batch.py`
- `GameSpec` gains: `patch_files`, `patch_sources`, `auto_search_patches`,
  `patch_candidates` — all with safe `field(default_factory=list)` defaults
- `Pipeline.run()` — step 3b inserts patch application between normalise and artwork
- `Pipeline._step_apply_patches()` — handles local files, GUI-selected candidates,
  and optional auto-search

---

## [1.0.0] — Python Edition Initial Release

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
