"""Quick smoke test – BGM search, download, AT3 conversion."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from modules.bgm import get_bgm_sources, search_and_get_bgm
from modules.constants import CACHE_DIR

import tempfile

def log(m):
    print(" ", m)

print("=== BGM tool availability ===")
srcs = get_bgm_sources()
for k, v in srcs.items():
    mark = "OK" if v else "MISSING"
    print(f"  {k:12s}: {mark}")

if not srcs["yt_dlp"]:
    print("\nyt-dlp not available. Install: pip install yt-dlp")
    sys.exit(0)

if not srcs["at3tool"]:
    print("\nat3tool.exe not found – AT3 output will be skipped.")

print()
print("=== BGM fetch test: Castlevania SotN ===")

dest = os.path.join(CACHE_DIR, "test_sotn.at3")

ok = search_and_get_bgm(
    game_name         = "Castlevania Symphony of the Night",
    serial            = "SLUS00067",
    dest_at3          = dest,
    progress_cb       = log,
)

print()
if ok and os.path.isfile(dest):
    sz = os.path.getsize(dest)
    print(f"PASSED – AT3 created: {dest}  ({sz:,} bytes)")
elif os.path.isfile(dest.replace(".at3", ".mp3")):
    print("PARTIAL – MP3 available but AT3 encoding skipped (at3tool missing or ffmpeg needed)")
else:
    print("FAILED or skipped – check log above")
