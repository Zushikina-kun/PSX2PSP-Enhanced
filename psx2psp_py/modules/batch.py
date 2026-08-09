"""
batch.py – Batch conversion manager.

Orchestrates the full pipeline for one or many games:
  1. Detect game serial (game_db.detect_serial)
  2. Look up gameInfo.db entry
  3. Normalise image (CUE→BIN if needed)
  4. Fetch + build artwork bundle
  5. Search + download + convert BGM → AT3
  6. Run popstation conversion
  7. Report per-game status + aggregate stats
"""

import os
import threading
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any

from .game_db   import detect_serial, lookup_game, GameInfo, normalise_serial
from .artwork   import build_artwork_bundle, ArtworkBundle
from .bgm       import search_and_get_bgm
from .converter import ConversionJob, normalise_to_iso
from .constants import OUTPUT_DIR, DEFAULT_COMPRESS


# ── Per-game job spec ─────────────────────────────────────────────────────────

@dataclass
class GameSpec:
    """
    All the information needed to convert one PSX game.
    Populated partly by the user, partly by auto-detection.
    """
    # Required inputs
    disc_paths: List[str]                    # 1–4 disc images (BIN/CUE/ISO)

    # Auto-filled (or user-overridden)
    serial:        str  = ""
    game_title:    str  = ""
    save_title:    str  = ""
    game_id:       str  = ""                 # hyphenated, e.g. SLUS-01234
    save_id:       str  = ""                 # e.g. SLUS01234
    video_format:  str  = "NTSC"

    # Asset overrides (leave empty for auto)
    custom_icon0:  str  = ""
    custom_pic1:   str  = ""
    custom_snd0:   str  = ""                 # path to audio file (any format)

    # Options
    output_dir:    str  = ""
    comp_level:    int  = DEFAULT_COMPRESS
    apply_patches: bool = False
    fetch_artwork: bool = True
    fetch_bgm:     bool = True
    loop_bgm:      bool = True
    bgm_sources:   Optional[list] = None  # None = use default order (khinsider, archive, youtube)


@dataclass
class GameResult:
    spec:       GameSpec
    success:    bool       = False
    pbp_path:   str        = ""
    error_msg:  str        = ""
    log:        List[str]  = field(default_factory=list)

    def add_log(self, msg: str):
        self.log.append(msg)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class Pipeline:
    """
    Runs the full conversion pipeline for a single GameSpec.
    All heavy work happens in the temp directory; output PBP is placed in
    spec.output_dir (falls back to OUTPUT_DIR constant).
    """

    def __init__(
        self,
        spec:        GameSpec,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        # progress_cb(message, bytes_done, bytes_total)
    ):
        self.spec        = spec
        self._progress   = progress_cb
        self._result     = GameResult(spec=spec)
        self._total      = 0
        self._cancel     = threading.Event()

    # --- helpers ---

    def _log(self, msg: str):
        self._result.add_log(msg)
        if self._progress:
            self._progress(msg, 0, self._total)

    def _prog(self, done: int):
        if self._progress:
            self._progress("Converting…", done, self._total)

    def _set_total(self, total: int):
        self._total = total

    def cancel(self):
        self._cancel.set()

    # --- steps ---

    def _step_detect_serial(self) -> bool:
        spec = self.spec
        if spec.serial:
            return True
        self._log("Detecting game serial…")
        for path in spec.disc_paths:
            serial = detect_serial(path)
            if serial:
                spec.serial = serial
                self._log(f"Detected serial: {serial}")
                return True
        self._log("WARNING: Could not detect serial from image.")
        spec.serial = "UNKN00000"
        return True  # continue with placeholder serial

    def _step_lookup_db(self) -> bool:
        spec = self.spec
        if spec.game_title:
            return True  # already filled
        self._log(f"Looking up gameInfo.db for {spec.serial}…")
        gi: Optional[GameInfo] = lookup_game(spec.serial)
        if gi:
            if not spec.game_title:
                spec.game_title   = gi.game_name
            if not spec.save_title:
                spec.save_title   = gi.save_desc or gi.game_name
            if not spec.game_id:
                spec.game_id      = gi.game_id_formatted
            if not spec.save_id:
                spec.save_id      = gi.save_folder
            spec.video_format = gi.video_format
            self._log(f"Found: {gi.game_name} ({gi.video_format})")
        else:
            self._log("Game not found in DB; using serial as title.")
            if not spec.game_title:
                spec.game_title = spec.serial
            if not spec.save_title:
                spec.save_title = spec.serial
            if not spec.game_id:
                spec.game_id = spec.serial
            if not spec.save_id:
                spec.save_id = normalise_serial(spec.serial)
        return True

    def _step_normalise_images(self, tmp: str) -> Optional[List[str]]:
        """Ensure all disc images are plain BIN/ISO (no multi-track CUE)."""
        normalised = []
        for path in self.spec.disc_paths:
            self._log(f"Normalising: {os.path.basename(path)}…")
            norm = normalise_to_iso(path, tmp, self._log)
            if not os.path.isfile(norm):
                self._result.error_msg = f"Could not access image: {norm}"
                return None
            normalised.append(norm)
        return normalised

    def _step_fetch_artwork(self, tmp: str) -> ArtworkBundle:
        spec    = self.spec
        self._log(f"Fetching artwork for '{spec.game_title}'…")
        bundle  = build_artwork_bundle(
            serial                = normalise_serial(spec.serial),
            game_name             = spec.game_title,
            progress_cb           = self._log,
            custom_cover_path     = spec.custom_icon0 or None,
            fetch_screenshot      = True,
            animate_icon          = False,   # skip in batch for speed
        )
        bundle.save_all(tmp)
        return bundle

    def _step_fetch_bgm(self, tmp: str) -> str:
        """Return path to SND0.AT3 (may be empty string if unavailable)."""
        spec   = self.spec
        at3out = os.path.join(tmp, "SND0.AT3")

        if spec.custom_snd0 and os.path.isfile(spec.custom_snd0):
            from .bgm import convert_to_at3
            self._log(f"Converting custom BGM: {os.path.basename(spec.custom_snd0)}…")
            ok = convert_to_at3(spec.custom_snd0, at3out, self._log, spec.loop_bgm)
            return at3out if ok else ""

        if spec.fetch_bgm:
            self._log(f"Searching BGM for '{spec.game_title}'…")
            ok = search_and_get_bgm(
                game_name   = spec.game_title,
                serial      = normalise_serial(spec.serial),
                dest_at3    = at3out,
                progress_cb = self._log,
                loop        = spec.loop_bgm,
                sources     = getattr(spec, "bgm_sources", None),
            )
            return at3out if ok else ""

        return ""

    def _step_convert(self, iso_paths: List[str],
                      bundle: ArtworkBundle, at3_path: str,
                      tmp: str) -> bool:
        spec      = self.spec
        out_dir   = spec.output_dir or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        # Choose PBP filename from save folder or title
        safe_title = "".join(c for c in spec.game_title
                             if c.isalnum() or c in " _-")[:40].strip()
        pbp_name   = f"{safe_title} [{spec.serial}].PBP"
        pbp_path   = os.path.join(out_dir, pbp_name)

        # Resolve image asset paths
        icon0 = (spec.custom_icon0
                 if spec.custom_icon0 and os.path.isfile(spec.custom_icon0)
                 else os.path.join(tmp, "ICON0.PNG"))
        pic0  = os.path.join(tmp, "PIC0.PNG")
        pic1  = (spec.custom_pic1
                 if spec.custom_pic1 and os.path.isfile(spec.custom_pic1)
                 else os.path.join(tmp, "PIC1.PNG"))
        boot  = os.path.join(tmp, "BOOT.PNG")
        snd0  = at3_path if os.path.isfile(at3_path) else ""

        job = ConversionJob(
            iso_paths     = iso_paths,
            output_pbp    = pbp_path,
            game_title    = spec.game_title,
            save_title    = spec.save_title,
            game_id       = spec.game_id,
            save_id       = spec.save_id,
            icon0_path    = icon0  if os.path.isfile(icon0)  else "",
            icon1_path    = "",
            pic0_path     = pic0   if os.path.isfile(pic0)   else "",
            pic1_path     = pic1   if os.path.isfile(pic1)   else "",
            snd0_path     = snd0,
            boot_path     = boot   if os.path.isfile(boot)   else "",
            comp_level    = spec.comp_level,
            apply_patches = spec.apply_patches,
            src_is_pbp    = False,
        )

        self._log(f"Converting → {pbp_name}")
        ok = job.run_sync(
            total_size_cb = self._set_total,
            progress_cb   = self._prog,
            log_cb        = self._log,
        )
        if ok:
            self._result.pbp_path = pbp_path
        else:
            self._result.error_msg = job._error_msg or "Conversion failed."
        return ok

    # --- main run ---

    def run(self) -> GameResult:
        spec = self.spec

        if not spec.disc_paths:
            self._result.error_msg = "No disc images provided."
            return self._result

        with tempfile.TemporaryDirectory(prefix="psx2psp_") as tmp:
            # 1. Serial detection
            if self._cancel.is_set(): return self._result
            self._step_detect_serial()

            # 2. DB lookup
            if self._cancel.is_set(): return self._result
            self._step_lookup_db()

            # 3. Normalise images
            if self._cancel.is_set(): return self._result
            iso_paths = self._step_normalise_images(tmp)
            if iso_paths is None:
                return self._result

            # 4. Artwork
            if self._cancel.is_set(): return self._result
            if spec.fetch_artwork:
                bundle = self._step_fetch_artwork(tmp)
            else:
                from .artwork import ArtworkBundle, build_artwork_bundle
                # Generate placeholders only (no online search)
                bundle = build_artwork_bundle(
                    normalise_serial(spec.serial),
                    spec.game_title,
                    self._log,
                    custom_cover_path=spec.custom_icon0 or None,
                )
                bundle.save_all(tmp)

            # 5. BGM
            if self._cancel.is_set(): return self._result
            at3_path = self._step_fetch_bgm(tmp)

            # 6. Convert
            if self._cancel.is_set(): return self._result
            ok = self._step_convert(iso_paths, bundle, at3_path, tmp)
            self._result.success = ok

        return self._result


# ── Batch runner ──────────────────────────────────────────────────────────────

class BatchRunner:
    """
    Run multiple GameSpec objects sequentially (or in a background thread).

    Attributes:
        specs:      list of GameSpec to process
        results:    populated as each spec finishes
    """

    def __init__(
        self,
        specs:            List[GameSpec],
        job_progress_cb:  Optional[Callable[[str, int, int], None]] = None,
        job_done_cb:      Optional[Callable[[GameResult], None]]    = None,
        all_done_cb:      Optional[Callable[[List[GameResult]], None]] = None,
    ):
        self.specs           = specs
        self._job_prog_cb    = job_progress_cb
        self._job_done_cb    = job_done_cb
        self._all_done_cb    = all_done_cb
        self.results:        List[GameResult] = []
        self._cancel         = threading.Event()
        self._thread:        Optional[threading.Thread] = None
        self._current_pipe:  Optional[Pipeline]         = None

    def start_async(self):
        """Run batch in a daemon background thread."""
        self._thread = threading.Thread(target=self._run_all, daemon=True)
        self._thread.start()

    def run_sync(self) -> List[GameResult]:
        """Run batch synchronously and return all results."""
        self._run_all()
        return self.results

    def cancel(self):
        self._cancel.set()
        if self._current_pipe:
            self._current_pipe.cancel()

    def _run_all(self):
        self.results.clear()
        for i, spec in enumerate(self.specs):
            if self._cancel.is_set():
                break
            pipe = Pipeline(spec, self._job_prog_cb)
            self._current_pipe = pipe
            result = pipe.run()
            self.results.append(result)
            if self._job_done_cb:
                self._job_done_cb(result)
        if self._all_done_cb:
            self._all_done_cb(self.results)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> Dict[str, Any]:
        total   = len(self.results)
        success = sum(1 for r in self.results if r.success)
        failed  = total - success
        return {"total": total, "success": success, "failed": failed}
