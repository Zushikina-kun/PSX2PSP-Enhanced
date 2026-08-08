"""
Test at3tool and lame directly using the bundled MP3 files in the project root.
"""
import sys, os, shutil, tempfile
sys.path.insert(0, os.path.dirname(__file__))

from modules.bgm import _wav_to_at3, _run, _at3tool_available, _lame_available
from modules.constants import AT3TOOL_EXE, LAME_EXE, ROOT_DIR

# Find any MP3 in the project root
mp3_files = [f for f in os.listdir(ROOT_DIR) if f.lower().endswith(".mp3")]
print(f"Found MP3 files: {mp3_files}")

if not mp3_files:
    print("No MP3 files found – skipping lame/at3 test")
    sys.exit(0)

mp3_src = os.path.join(ROOT_DIR, mp3_files[0])
print(f"\nUsing: {mp3_src}  ({os.path.getsize(mp3_src):,} bytes)")

with tempfile.TemporaryDirectory(prefix="at3_test_") as tmp:
    wav_path = os.path.join(tmp, "test.wav")
    at3_path = os.path.join(tmp, "test.at3")

    # Step 1: MP3 → WAV via lame --decode
    print("\n--- lame --decode ---")
    if _lame_available():
        ok = _run([LAME_EXE, "--decode", "--quiet", mp3_src, wav_path], timeout=60)
        if ok and os.path.isfile(wav_path):
            sz = os.path.getsize(wav_path)
            print(f"  WAV created: {sz:,} bytes  ({'OK' if sz > 1000 else 'EMPTY!'})")
        else:
            print("  FAILED")
    else:
        print("  lame not available")

    # Step 2: WAV → AT3 via at3tool
    print("\n--- at3tool encode ---")
    if _at3tool_available() and os.path.isfile(wav_path):
        ok = _wav_to_at3(wav_path, at3_path, bitrate=132, loop=True)
        if ok and os.path.isfile(at3_path):
            sz = os.path.getsize(at3_path)
            print(f"  AT3 created: {sz:,} bytes  ({'OK' if sz > 1000 else 'EMPTY!'})")
            # Copy to cache for reference
            out = os.path.join(os.path.dirname(__file__), "cache", "test_at3.at3")
            shutil.copy2(at3_path, out)
            print(f"  Saved to cache: {out}")
        else:
            # Run at3tool directly and capture output for diagnosis
            import subprocess
            res = subprocess.run(
                [AT3TOOL_EXE, "-e", "-br", "132", "-wholeloop", wav_path, at3_path],
                capture_output=True, text=True, timeout=60
            )
            print(f"  at3tool stdout: {res.stdout.strip()}")
            print(f"  at3tool stderr: {res.stderr.strip()}")
            print(f"  returncode: {res.returncode}")
    else:
        if not _at3tool_available():
            print("  at3tool not found")
        else:
            print("  No WAV to encode")

print("\nDone.")
