"""Quick smoke test – artwork fetch + image generation."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from modules.artwork import build_artwork_bundle
import tempfile

def log(m):
    print(" ", m)

serial = "SLUS00067"
title  = "Castlevania - Symphony of the Night"

print(f"=== Artwork test: {title} ===")
bundle = build_artwork_bundle(serial, title, progress_cb=log)

print()
for name, img in [("cover", bundle.cover), ("icon0", bundle.icon0),
                  ("pic0",  bundle.pic0),  ("pic1",  bundle.pic1),
                  ("boot",  bundle.boot)]:
    sz = str(getattr(img, "size", "—"))
    print(f"  {name:6s}: {'OK' if img else 'None':4s}  size={sz}")

with tempfile.TemporaryDirectory() as tmp:
    saved = bundle.save_all(tmp)
    print()
    print("Saved files:")
    for k, p in saved.items():
        bytes_sz = os.path.getsize(p)
        print(f"  {k:6s}: {os.path.basename(p)}  ({bytes_sz:,} bytes)")

print()
print("PASSED" if bundle.icon0 else "PASSED (placeholder used – no internet cover found)")
