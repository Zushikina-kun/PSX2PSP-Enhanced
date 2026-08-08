"""
Full smoke test: game_db + artwork + bgm (local MP3) in one run.
No internet required for game_db and artwork (uses cache).
BGM uses local MP3 from project root.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def check(name, ok, detail=""):
    mark = PASS if ok else FAIL
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok))

print("=" * 60)
print("PSX2PSP Enhanced — Full Smoke Test")
print("=" * 60)

# ── 1. Module imports ─────────────────────────────────────────────────────────
print("\n[1] Module imports")
try:
    from modules.constants import ROOT_DIR, AT3TOOL_EXE, LAME_EXE, CACHE_DIR
    from modules.game_db   import lookup_game, search_games, detect_serial, db_size, normalise_serial
    from modules.artwork   import build_artwork_bundle
    from modules.bgm       import convert_to_at3, get_bgm_sources
    from modules.converter import ConversionJob, normalise_to_iso
    from modules.batch     import GameSpec, Pipeline, BatchRunner
    check("All modules import", True)
except Exception as e:
    check("All modules import", False, str(e))
    sys.exit(1)

# ── 2. game_db ────────────────────────────────────────────────────────────────
print("\n[2] game_db")
n = db_size()
check("DB loaded", n > 0, f"{n:,} entries")

gi = lookup_game("SLUS00067")
check("lookup SLUS00067", gi is not None, gi.game_name if gi else "")
if gi:
    check("gi.game_id_formatted", gi.game_id_formatted.startswith("SLUS"), gi.game_id_formatted)
    check("gi.serial_norm",       gi.serial_norm == "SLUS00067", gi.serial_norm)
    check("gi.save_desc",         bool(gi.save_desc), repr(gi.save_desc))
    check("gi.save_folder",       bool(gi.save_folder), gi.save_folder)

gi2 = lookup_game("SCUS94221")
check("lookup SCUS94221 (FF Tactics)", gi2 is not None, gi2.game_name if gi2 else "")

hits = search_games("castlevania", max_results=5)
check("search 'castlevania'", len(hits) > 0, f"{len(hits)} results")

# ── 3. Artwork (from cache or network) ────────────────────────────────────────
print("\n[3] Artwork")
log_lines = []
bundle = build_artwork_bundle("SLUS00067", "Castlevania Symphony of the Night",
                              progress_cb=log_lines.append)
for l in log_lines:
    print(f"    {l}")
check("cover obtained",  bundle.cover is not None)
check("icon0 144x80",    bundle.icon0 is not None and bundle.icon0.size == (144, 80),
      str(getattr(bundle.icon0, "size", "None")))
check("pic0 480x272",    bundle.pic0  is not None and bundle.pic0.size  == (480, 272))
check("pic1 480x272",    bundle.pic1  is not None and bundle.pic1.size  == (480, 272))
check("boot 480x272",    bundle.boot  is not None and bundle.boot.size  == (480, 272))

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    saved = bundle.save_all(tmp)
    check("save_all returns 4 files", len(saved) == 4, str(list(saved.keys())))
    for k, p in saved.items():
        sz = os.path.getsize(p)
        check(f"  {k}.PNG > 0 bytes", sz > 100, f"{sz:,} B")

# ── 4. BGM tools ──────────────────────────────────────────────────────────────
print("\n[4] BGM tools")
srcs = get_bgm_sources()
for k, v in srcs.items():
    check(f"  {k}", v, "present" if v else "MISSING")

# ── 5. AT3 pipeline (local MP3) ───────────────────────────────────────────────
print("\n[5] AT3 conversion (local MP3 → WAV → AT3)")
mp3_files = [f for f in os.listdir(ROOT_DIR) if f.lower().endswith(".mp3")]
if mp3_files:
    mp3 = os.path.join(ROOT_DIR, mp3_files[0])
    at3_out = os.path.join(CACHE_DIR, "smoke_test.at3")
    at3_log = []
    t0 = time.time()
    ok = convert_to_at3(mp3, at3_out, progress_cb=at3_log.append)
    elapsed = time.time() - t0
    for l in at3_log:
        print(f"    {l}")
    check("convert_to_at3 returned True", ok)
    if os.path.isfile(at3_out):
        sz = os.path.getsize(at3_out)
        check("AT3 file > 1MB", sz > 1_000_000, f"{sz:,} bytes in {elapsed:.1f}s")
    else:
        check("AT3 file created", False)
else:
    print("  No MP3 in ROOT_DIR — skipping AT3 test")

# ── 6. GameSpec / Pipeline dataclass validation ───────────────────────────────
print("\n[6] GameSpec / Pipeline")
spec = GameSpec(
    disc_paths    = ["dummy.bin"],
    serial        = "SLUS00067",
    game_title    = "Castlevania",
    output_dir    = os.path.join(os.path.dirname(__file__), "output"),
    fetch_artwork = False,
    fetch_bgm     = False,
)
check("GameSpec created", isinstance(spec, GameSpec))
pipe = Pipeline(spec, progress_cb=lambda m, d, t: None)
check("Pipeline created", pipe is not None)

runner = BatchRunner([spec])
check("BatchRunner created", runner is not None)
check("BatchRunner.is_running=False", not runner.is_running)
check("BatchRunner.stats", runner.stats == {"total": 0, "success": 0, "failed": 0})

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f"Results: {passed}/{total} passed" + (f"  ({failed} FAILED)" if failed else "  — ALL PASS ✓"))
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
