"""Test at3tool from the release folder (simulates frozen EXE environment)."""
import sys, os, subprocess, tempfile

# Simulate frozen EXE in release folder
sys.frozen = True
sys.executable = (
    r"D:\Downloads\Compressed\PSP\PSXEboot\PSX2PSP\dist\release"
    r"\PSX2PSP_Enhanced_v1.1.2\PSX2PSP_Enhanced.exe"
)

import importlib
sys.path.insert(0, os.path.dirname(__file__))
import modules.constants as mc
importlib.reload(mc)

at3tool = mc.AT3TOOL_EXE
lame    = mc.LAME_EXE
root    = mc.ROOT_DIR
mp3     = r"D:\Downloads\Compressed\PSP\PSXEboot\PSX2PSP\01 Title Demo.mp3"

print("ROOT_DIR:  ", root)
print("at3tool:   ", at3tool, "exists:", os.path.isfile(at3tool))
print("lame:      ", lame,    "exists:", os.path.isfile(lame))

with tempfile.TemporaryDirectory(prefix="psx2psp_at3_") as tmp:
    wav = os.path.join(tmp, "ready.wav")
    at3 = os.path.join(tmp, "test.at3")

    # Step 1: lame decode
    lame_cmd = f'"{lame}" --decode --quiet "{mp3}" "{wav}"'
    r1 = subprocess.run(lame_cmd, shell=True, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    print(f"lame rc={r1.returncode}  wav={'%d bytes' % os.path.getsize(wav) if os.path.isfile(wav) else 'MISSING'}")

    # Step 2: at3tool encode with cwd=root
    at3_cmd = f'"{at3tool}" -e -br 132 -wholeloop "{wav}" "{at3}"'
    print(f"at3_cmd: {at3_cmd}")
    print(f"cwd:     {root}")
    r2 = subprocess.run(at3_cmd, cwd=root, shell=True, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    print(f"at3tool rc={r2.returncode}")
    print(f"  stdout: {r2.stdout[:120]}")
    print(f"  stderr: {r2.stderr[:120]}")
    if os.path.isfile(at3):
        print(f"AT3={os.path.getsize(at3):,} bytes  PASS")
    else:
        print("AT3 MISSING  FAIL")

del sys.frozen
