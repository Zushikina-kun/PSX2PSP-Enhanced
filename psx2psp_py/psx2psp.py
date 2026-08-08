#!/usr/bin/env python3
"""
psx2psp.py – PSX2PSP Enhanced (Python Edition) launcher.

Usage:
    python psx2psp.py                          # launch GUI
    python psx2psp.py "game.cue"               # open GUI with file pre-loaded
    python psx2psp.py --cli "game.cue" [opts]  # headless CLI conversion

CLI Options:
    --cli               run without GUI
    --out DIR           output directory  (default: psx2psp_py/output)
    --title "TITLE"     override game title
    --no-artwork        skip artwork fetch
    --no-bgm            skip BGM fetch
    --compress N        compression 0-9  (default: 9)
    --patches           apply PAL->NTSC patches
"""

import os
import sys
import argparse

# ── Add the psx2psp_py directory to sys.path so 'modules' is importable ───────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Ensure cache/output dirs exist early
from modules.constants import CACHE_DIR, OUTPUT_DIR
os.makedirs(CACHE_DIR,  exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── CLI mode ──────────────────────────────────────────────────────────────────

def run_cli(args):
    from modules.batch import GameSpec, BatchRunner, GameResult

    disc_paths = args.files
    if not disc_paths:
        print("ERROR: Provide at least one disc image.", file=sys.stderr)
        sys.exit(1)

    spec = GameSpec(
        disc_paths    = disc_paths,
        game_title    = args.title or "",
        output_dir    = args.out or OUTPUT_DIR,
        comp_level    = args.compress,
        apply_patches = args.patches,
        fetch_artwork = not args.no_artwork,
        fetch_bgm     = not args.no_bgm,
    )

    def _progress(msg: str, done: int, total: int):
        if total > 0:
            pct = int(done / total * 100)
            print(f"  [{pct:3d}%] {msg}", flush=True)
        else:
            print(f"  {msg}", flush=True)

    def _done(result: GameResult):
        if result.success:
            print(f"\n✅  SUCCESS: {result.pbp_path}")
        else:
            print(f"\n❌  FAILED:  {result.error_msg}")
        for line in result.log[-10:]:   # last 10 log lines
            print(f"   {line}")

    print("PSX2PSP Enhanced – CLI Mode")
    print(f"Input:  {disc_paths[0]}")
    print(f"Output: {spec.output_dir}\n")

    runner = BatchRunner([spec],
                         job_progress_cb=_progress,
                         job_done_cb=_done)
    results = runner.run_sync()
    sys.exit(0 if all(r.success for r in results) else 1)


# ── GUI mode ──────────────────────────────────────────────────────────────────

def run_gui(preload_file: str = ""):
    try:
        from modules.gui import App
    except ImportError as e:
        print(f"GUI import error: {e}", file=sys.stderr)
        print("Make sure Pillow is installed: pip install pillow", file=sys.stderr)
        sys.exit(1)

    app = App()
    if preload_file and os.path.isfile(preload_file):
        # Pre-load the file after mainloop starts
        app.after(200, lambda: app._menu_open_path(preload_file))

    app.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PSX2PSP Enhanced – Python Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    parser.add_argument("files", nargs="*",
                        help="Disc image(s) (.bin .iso .cue)")
    parser.add_argument("--cli",         action="store_true",
                        help="Headless CLI mode (no GUI)")
    parser.add_argument("--out",         default="",
                        help="Output directory")
    parser.add_argument("--title",       default="",
                        help="Override game title")
    parser.add_argument("--no-artwork",  action="store_true",
                        help="Skip artwork fetch")
    parser.add_argument("--no-bgm",      action="store_true",
                        help="Skip BGM fetch/conversion")
    parser.add_argument("--compress",    type=int, default=9,
                        choices=range(0, 10), metavar="N",
                        help="Compression level 0-9 (default: 9)")
    parser.add_argument("--patches",     action="store_true",
                        help="Apply PAL->NTSC patches")

    args = parser.parse_args()

    if args.cli or (args.files and len(args.files) == 1
                    and args.files[0].startswith("--")):
        run_cli(args)
    elif args.files and args.cli:
        run_cli(args)
    elif args.files and not args.cli:
        # Single file passed without --cli: open GUI with pre-load
        run_gui(preload_file=args.files[0])
    else:
        run_gui()


if __name__ == "__main__":
    main()
