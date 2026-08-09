"""
converter.py – PSX → PSP PBP conversion engine.

Wraps popstation.dll via ctypes, replicating the ConvertIsoInfo / ExtractIsoInfo
structures defined in Files/Popstation src/popstation.h.

Also handles:
  - CUE→BIN normalisation via PocketISO.exe (if needed)
  - Reading/writing the patches.ini patch list
  - Multi-disc support
  - Progress callbacks via a Windows message pump on a background thread
"""

import os
import re
import struct
import subprocess
import sys
import threading
import ctypes
import configparser
import tempfile
from typing import Optional, Callable, List, Tuple

from .constants import (
    POPSTATION_DLL, BASE_PBP, DATA_PSP, POCKETISO_EXE,
    PATCHES_INI, SETTINGS_INI, DEFAULT_COMPRESS,
)

# ── 32-bit Python detection ───────────────────────────────────────────────────

def _find_python32() -> str:
    """
    Return path to a 32-bit python.exe if available, else empty string.
    Checks:
      1. PYTHON32 environment variable
      2. Common installation paths
    """
    env_py32 = os.environ.get("PYTHON32", "")
    if env_py32 and os.path.isfile(env_py32):
        return env_py32
    candidates = [
        r"C:\Python312-32\python.exe",
        r"C:\Python311-32\python.exe",
        r"C:\Python310-32\python.exe",
        r"C:\Python39-32\python.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312-32\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python311-32\python.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""

# ── ctypes structures (mirrors popstation.h) ──────────────────────────────────

MAX_PATH = 0xFF  # 255 chars as in the header


class PatchData(ctypes.Structure):
    _pack_  = 1
    _fields_ = [
        ("newData",       ctypes.c_char),
        ("dataPosition",  ctypes.c_uint32),
    ]


class MultiDiscInfo(ctypes.Structure):
    _pack_  = 1
    _fields_ = [
        ("fileCount",   ctypes.c_char),
        ("srcISO1",     ctypes.c_char * MAX_PATH),
        ("srcISO2",     ctypes.c_char * MAX_PATH),
        ("srcISO3",     ctypes.c_char * MAX_PATH),
        ("srcISO4",     ctypes.c_char * MAX_PATH),
        ("gameTitle1",  ctypes.c_char * MAX_PATH),
        ("gameTitle2",  ctypes.c_char * MAX_PATH),
        ("gameTitle3",  ctypes.c_char * MAX_PATH),
        ("gameTitle4",  ctypes.c_char * MAX_PATH),
        ("gameID1",     ctypes.c_char * MAX_PATH),
        ("gameID2",     ctypes.c_char * MAX_PATH),
        ("gameID3",     ctypes.c_char * MAX_PATH),
        ("gameID4",     ctypes.c_char * MAX_PATH),
    ]


class ConvertIsoInfo(ctypes.Structure):
    _pack_  = 1
    _fields_ = [
        ("callback",      ctypes.c_void_p),
        ("base",          ctypes.c_char * MAX_PATH),
        ("data_psp",      ctypes.c_char * MAX_PATH),
        ("srcISO",        ctypes.c_char * MAX_PATH),
        ("dstPBP",        ctypes.c_char * MAX_PATH),
        ("pic0",          ctypes.c_char * MAX_PATH),
        ("pic1",          ctypes.c_char * MAX_PATH),
        ("icon0",         ctypes.c_char * MAX_PATH),
        ("icon1",         ctypes.c_char * MAX_PATH),
        ("snd0",          ctypes.c_char * MAX_PATH),
        ("boot",          ctypes.c_char * MAX_PATH),
        ("srcIsPbp",      ctypes.c_bool),
        ("gameTitle",     ctypes.c_char * MAX_PATH),
        ("saveTitle",     ctypes.c_char * MAX_PATH),
        ("gameID",        ctypes.c_char * MAX_PATH),
        ("saveID",        ctypes.c_char * MAX_PATH),
        ("compLevel",     ctypes.c_int),
        ("tocSize",       ctypes.c_int),
        ("tocData",       ctypes.c_void_p),
        ("multiDiscInfo", MultiDiscInfo),
        ("patchCount",    ctypes.c_int),
        ("patchData",     ctypes.c_void_p),
    ]


class ExtractIsoInfo(ctypes.Structure):
    _pack_  = 1
    _fields_ = [
        ("callback",  ctypes.c_void_p),
        ("srcPBP",    ctypes.c_char * MAX_PATH),
        ("dstISO",    ctypes.c_char * MAX_PATH),
    ]


# WM_USER + offsets used by popstation.dll
WM_CONVERT_SIZE     = 0x0400 + 200
WM_CONVERT_PROGRESS = 0x0400 + 201
WM_CONVERT_ERROR    = 0x0400 + 202
WM_CONVERT_DONE     = 0x0400 + 203
WM_EXTRACT_SIZE     = 0x0400 + 100
WM_EXTRACT_PROGRESS = 0x0400 + 101
WM_EXTRACT_ERROR    = 0x0400 + 102
WM_EXTRACT_DONE     = 0x0400 + 103


# ── DLL loader ────────────────────────────────────────────────────────────────

_dll_lock   = threading.Lock()
_dll_handle = None


def _load_dll():
    global _dll_handle
    with _dll_lock:
        if _dll_handle is None:
            if not os.path.isfile(POPSTATION_DLL):
                raise FileNotFoundError(f"popstation.dll not found: {POPSTATION_DLL}")
            _dll_handle = ctypes.WinDLL(POPSTATION_DLL)
            # Prototype the exported functions
            _dll_handle.convert.restype  = ctypes.c_void_p   # HANDLE
            _dll_handle.convert.argtypes = [ConvertIsoInfo]
            _dll_handle.cancel_convert.restype  = None
            _dll_handle.cancel_convert.argtypes = []
            _dll_handle.extract.restype  = ctypes.c_void_p
            _dll_handle.extract.argtypes = [ExtractIsoInfo]
            _dll_handle.cancel_extract.restype  = None
            _dll_handle.cancel_extract.argtypes = []
    return _dll_handle


# ── Patch loader ──────────────────────────────────────────────────────────────

def load_patches() -> List[Tuple[str, bytes, bytes]]:
    """
    Parse patches.ini.
    Returns list of (name, searchFor_bytes, replaceWith_bytes).
    """
    patches = []
    if not os.path.isfile(PATCHES_INI):
        return patches
    cfg = configparser.ConfigParser()
    cfg.read(PATCHES_INI, encoding="utf-8")
    for section in cfg.sections():
        name    = cfg.get(section, "name",         fallback="")
        search  = cfg.get(section, "searchFor",    fallback="")
        replace = cfg.get(section, "replaceWith",  fallback="")
        try:
            sb = bytes.fromhex(search)
            rb = bytes.fromhex(replace)
            patches.append((name, sb, rb))
        except ValueError:
            pass
    return patches


def _build_patch_array(iso_path: str,
                       active_patch_names: Optional[List[str]] = None
                       ) -> Tuple[int, Optional[ctypes.Array]]:
    """
    Scan the ISO for matching patches and build a PatchData ctypes array.
    Returns (count, ctypes_array_or_None).
    """
    all_patches = load_patches()
    if not all_patches:
        return 0, None

    # Read entire ISO into memory for scanning (max ~700 MB – fine for RAM)
    try:
        with open(iso_path, "rb") as f:
            data = f.read()
    except OSError:
        return 0, None

    hits: List[PatchData] = []
    for name, search, replace in all_patches:
        if active_patch_names is not None and name not in active_patch_names:
            continue
        if len(search) != len(replace):
            continue
        pos = 0
        while True:
            idx = data.find(search, pos)
            if idx == -1:
                break
            for i, byte_val in enumerate(replace):
                pd           = PatchData()
                pd.newData   = bytes([byte_val])
                pd.dataPosition = ctypes.c_uint32(idx + i)
                hits.append(pd)
            pos = idx + 1

    if not hits:
        return 0, None

    ArrayType = PatchData * len(hits)
    arr       = ArrayType(*hits)
    return len(hits), arr


# ── CUE/BIN normalisation ─────────────────────────────────────────────────────

def normalise_to_iso(path: str,
                     out_dir: Optional[str] = None,
                     progress_cb: Optional[Callable[[str], None]] = None
                     ) -> str:
    """
    If *path* is a .cue, resolve it to the underlying BIN/ISO.
    - Single-track CUE: return the referenced BIN directly (no tool needed).
    - Multi-track CUE:  use PocketISO to merge tracks.
    If it's already a .bin/.iso, return it unchanged.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".bin", ".iso", ".img"):
        return path  # already usable

    if ext == ".cue":
        cue_dir = os.path.dirname(path)

        # --- Parse CUE sheet to count FILE entries ---
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                cue_text = f.read()
        except Exception:
            cue_text = ""

        file_entries = re.findall(r'^\s*FILE\s+"?([^"\r\n]+?)"?\s+BINARY',
                                  cue_text, re.MULTILINE | re.IGNORECASE)

        if len(file_entries) == 1:
            # Single-track CUE: just return the referenced BIN
            bin_name = file_entries[0].strip()
            bin_path = os.path.join(cue_dir, bin_name)
            if os.path.isfile(bin_path):
                if progress_cb:
                    progress_cb(f"Single-track CUE → using BIN: {bin_name}")
                return bin_path
            # BIN not alongside CUE — fall through to PocketISO

        # --- Multi-track or unresolvable: use PocketISO ---
        if not os.path.isfile(POCKETISO_EXE):
            if progress_cb:
                progress_cb("PocketISO.exe not found; using CUE as-is.")
            return path

        out = out_dir or os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0]
        out_bin = os.path.join(out, base + "_combined.bin")

        if os.path.isfile(out_bin):
            return out_bin

        if progress_cb:
            progress_cb(f"Merging multi-track CUE via PocketISO…")

        try:
            # PocketISO is a legacy 32-bit Windows exe — needs shell=True
            if sys.platform == "win32":
                cmd_str = f'"{POCKETISO_EXE}" "{path}" "{out_bin}"'
                subprocess.run(cmd_str, shell=True,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=300)
            else:
                subprocess.run(
                    [POCKETISO_EXE, path, out_bin],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=300)
        except Exception as e:
            if progress_cb:
                progress_cb(f"PocketISO error: {e}")

        return out_bin if os.path.isfile(out_bin) else path

    return path  # unknown extension: pass through


# ── Windows message pump  (hidden HWND) ──────────────────────────────────────

class _ConvertCallback:
    """
    Creates a hidden Win32 window to receive WM_USER messages from the DLL.
    Drives the progress callback and signals completion.
    """

    def __init__(self,
                 total_size_cb:    Optional[Callable[[int], None]],
                 progress_cb:      Optional[Callable[[int], None]],
                 done_cb:          Optional[Callable[[bool], None]]):
        self._total_cb    = total_size_cb
        self._progress_cb = progress_cb
        self._done_cb     = done_cb
        self._hwnd        = None
        self._thread      = None
        self._ready       = threading.Event()
        self._cancelled   = False

    def start(self):
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _pump(self):
        """Create a message-only window and pump its message queue."""
        import ctypes as ct
        user32 = ct.windll.user32
        kernel32 = ct.windll.kernel32

        WNDPROCTYPE  = ct.WINFUNCTYPE(ct.c_long, ct.c_int, ct.c_uint,
                                      ct.c_uint, ct.c_long)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_CONVERT_SIZE or msg == WM_EXTRACT_SIZE:
                if self._total_cb:
                    self._total_cb(lparam)
            elif msg == WM_CONVERT_PROGRESS or msg == WM_EXTRACT_PROGRESS:
                if self._progress_cb:
                    self._progress_cb(lparam)
            elif msg == WM_CONVERT_DONE or msg == WM_EXTRACT_DONE:
                if self._done_cb:
                    self._done_cb(True)
            elif msg == WM_CONVERT_ERROR or msg == WM_EXTRACT_ERROR:
                if self._done_cb:
                    self._done_cb(False)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc_cb = WNDPROCTYPE(_wnd_proc)

        class WNDCLASSW(ct.Structure):
            _fields_ = [
                ("style",         ct.c_uint),
                ("lpfnWndProc",   WNDPROCTYPE),
                ("cbClsExtra",    ct.c_int),
                ("cbWndExtra",    ct.c_int),
                ("hInstance",     ct.c_void_p),
                ("hIcon",         ct.c_void_p),
                ("hCursor",       ct.c_void_p),
                ("hbrBackground", ct.c_void_p),
                ("lpszMenuName",  ct.c_wchar_p),
                ("lpszClassName", ct.c_wchar_p),
            ]

        cls_name = "PSX2PSP_CB_WND"
        wc = WNDCLASSW()
        wc.lpfnWndProc   = wnd_proc_cb
        wc.hInstance     = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls_name

        user32.RegisterClassW(ct.byref(wc))
        hwnd = user32.CreateWindowExW(
            0, cls_name, "PSX2PSP", 0,
            0, 0, 0, 0,
            ct.c_void_p(-3),  # HWND_MESSAGE
            None, wc.hInstance, None
        )
        self._hwnd = hwnd
        self._ready.set()

        msg = ct.wintypes.MSG()
        while user32.GetMessageW(ct.byref(msg), hwnd, 0, 0) != 0:
            user32.TranslateMessage(ct.byref(msg))
            user32.DispatchMessageW(ct.byref(msg))

    @property
    def hwnd(self) -> int:
        return self._hwnd or 0

    def stop(self):
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT


# ── Public ConversionJob ──────────────────────────────────────────────────────

class ConversionJob:
    """
    Encapsulates a single PSX→PSP conversion task.

    Usage:
        job = ConversionJob(...)
        job.start(progress_cb=..., done_cb=...)
        # or synchronously:
        success = job.run_sync()
    """

    def __init__(
        self,
        iso_paths:      List[str],       # 1–4 disc images
        output_pbp:     str,
        game_title:     str,
        save_title:     str,
        game_id:        str,             # e.g. SLUS-01234
        save_id:        str,             # e.g. SLUS01234
        icon0_path:     str  = "",
        icon1_path:     str  = "",
        pic0_path:      str  = "",
        pic1_path:      str  = "",
        snd0_path:      str  = "",
        boot_path:      str  = "",
        comp_level:     int  = DEFAULT_COMPRESS,
        apply_patches:  bool = False,
        patch_names:    Optional[List[str]] = None,
        src_is_pbp:     bool = False,
        toc_data:       Optional[bytes]    = None,
    ):
        self.iso_paths     = iso_paths
        self.output_pbp    = output_pbp
        self.game_title    = game_title
        self.save_title    = save_title
        self.game_id       = game_id
        self.save_id       = save_id
        self.icon0_path    = icon0_path
        self.icon1_path    = icon1_path
        self.pic0_path     = pic0_path
        self.pic1_path     = pic1_path
        self.snd0_path     = snd0_path
        self.boot_path     = boot_path
        self.comp_level    = comp_level
        self.apply_patches = apply_patches
        self.patch_names   = patch_names
        self.src_is_pbp    = src_is_pbp
        self.toc_data      = toc_data

        self._cancel_flag  = threading.Event()
        self._done_event   = threading.Event()
        self._success      = False
        self._error_msg    = ""

    def _encode(self, s: str) -> bytes:
        return s.encode("utf-8")[:MAX_PATH - 1] + b"\x00"

    def _build_info(self, callback_hwnd: int) -> ConvertIsoInfo:
        info = ConvertIsoInfo()
        info.callback  = ctypes.c_void_p(callback_hwnd)
        info.base      = self._encode(BASE_PBP)
        info.data_psp  = self._encode(DATA_PSP)
        info.srcISO    = self._encode(self.iso_paths[0])
        info.dstPBP    = self._encode(self.output_pbp)
        info.pic0      = self._encode(self.pic0_path)
        info.pic1      = self._encode(self.pic1_path)
        info.icon0     = self._encode(self.icon0_path)
        info.icon1     = self._encode(self.icon1_path)
        info.snd0      = self._encode(self.snd0_path)
        info.boot      = self._encode(self.boot_path)
        info.srcIsPbp  = ctypes.c_bool(self.src_is_pbp)
        info.gameTitle = self._encode(self.game_title)
        info.saveTitle = self._encode(self.save_title)
        info.gameID    = self._encode(self.game_id)
        info.saveID    = self._encode(self.save_id)
        info.compLevel = ctypes.c_int(self.comp_level)

        # TOC
        if self.toc_data:
            toc_buf       = (ctypes.c_char * len(self.toc_data))(*self.toc_data)
            info.tocSize  = ctypes.c_int(len(self.toc_data))
            info.tocData  = ctypes.cast(toc_buf, ctypes.c_void_p)
        else:
            info.tocSize  = 0
            info.tocData  = None

        # Multi-disc
        nd = len(self.iso_paths)
        if nd > 1:
            md              = info.multiDiscInfo
            md.fileCount    = bytes([nd])
            isos  = ["", "", "", ""]
            titles= ["", "", "", ""]
            ids   = ["", "", "", ""]
            for i, p in enumerate(self.iso_paths[:4]):
                isos[i]   = p
                titles[i] = self.game_title
                ids[i]    = self.game_id
            md.srcISO1    = self._encode(isos[0])
            md.srcISO2    = self._encode(isos[1])
            md.srcISO3    = self._encode(isos[2])
            md.srcISO4    = self._encode(isos[3])
            md.gameTitle1 = self._encode(titles[0])
            md.gameTitle2 = self._encode(titles[1])
            md.gameTitle3 = self._encode(titles[2])
            md.gameTitle4 = self._encode(titles[3])
            md.gameID1    = self._encode(ids[0])
            md.gameID2    = self._encode(ids[1])
            md.gameID3    = self._encode(ids[2])
            md.gameID4    = self._encode(ids[3])

        # Patches
        if self.apply_patches:
            count, arr = _build_patch_array(self.iso_paths[0], self.patch_names)
            info.patchCount = count
            if arr is not None:
                info.patchData = ctypes.cast(arr, ctypes.c_void_p)
                self._patch_arr_ref = arr  # keep alive

        return info

    def _run_via_bridge(self,
                        log_cb:       Optional[Callable[[str], None]],
                        total_cb:     Optional[Callable[[int], None]],
                        progress_cb:  Optional[Callable[[int], None]]) -> bool:
        """
        Fall back to popstation_bridge.py running under 32-bit Python.
        Returns True on success.
        """
        import json as _json

        py32 = _find_python32()
        if not py32:
            if log_cb:
                log_cb("No 32-bit Python found. Set PYTHON32=<path> env var.")
            return False

        bridge = os.path.join(os.path.dirname(__file__), "..", "popstation_bridge.py")
        bridge = os.path.normpath(bridge)
        if not os.path.isfile(bridge):
            if log_cb:
                log_cb(f"Bridge script not found: {bridge}")
            return False

        if log_cb:
            log_cb(f"Running bridge: {py32}")

        spec = _json.dumps({
            "iso_path":      self.iso_paths[0] if self.iso_paths else "",
            "output_pbp":    self.output_pbp,
            "game_title":    self.game_title,
            "save_title":    self.save_title,
            "game_id":       self.game_id,
            "save_id":       self.save_id,
            "icon0_path":    self.icon0_path,
            "pic0_path":     self.pic0_path,
            "pic1_path":     self.pic1_path,
            "snd0_path":     self.snd0_path,
            "boot_path":     self.boot_path,
            "comp_level":    self.comp_level,
            "apply_patches": self.apply_patches,   # FIX: was missing
        })

        try:
            proc = subprocess.Popen(
                [py32, bridge],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.stdin.write((spec + "\n").encode())
            proc.stdin.close()

            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                    mtype = msg.get("type", "")
                    if mtype == "log" and log_cb:
                        log_cb(msg.get("msg", ""))
                    elif mtype == "total" and total_cb:
                        total_cb(int(msg.get("value", 0)))
                    elif mtype == "progress" and progress_cb:
                        progress_cb(int(msg.get("value", 0)))
                    elif mtype == "done":
                        ok = bool(msg.get("success", False))
                        proc.wait()
                        return ok
                    elif mtype == "error" and log_cb:
                        log_cb(f"Bridge error: {msg.get('msg', '')}")
                except Exception:
                    if log_cb:
                        log_cb(line)

            proc.wait()
            return proc.returncode == 0

        except Exception as e:
            if log_cb:
                log_cb(f"Bridge launch failed: {e}")
            return False

    def cancel(self):
        self._cancel_flag.set()
        try:
            dll = _load_dll()
            dll.cancel_convert()
        except Exception:
            pass

    def run_sync(
        self,
        total_size_cb: Optional[Callable[[int], None]]  = None,
        progress_cb:   Optional[Callable[[int], None]]  = None,
        log_cb:        Optional[Callable[[str], None]]  = None,
    ) -> bool:
        """
        Run conversion synchronously (blocks until done).
        Returns True on success.
        """
        def _log(msg):
            if log_cb:
                log_cb(msg)

        # Verify prerequisites
        if not os.path.isfile(BASE_PBP):
            self._error_msg = f"BASE.PBP not found: {BASE_PBP}"
            _log(self._error_msg)
            return False
        for p in self.iso_paths:
            if not os.path.isfile(p):
                self._error_msg = f"Input file not found: {p}"
                _log(self._error_msg)
                return False

        os.makedirs(os.path.dirname(os.path.abspath(self.output_pbp)),
                    exist_ok=True)

        _log(f"Loading popstation.dll…")
        try:
            dll = _load_dll()
        except OSError as e:
            err = str(e)
            if "193" in err or "not a valid Win32 application" in err.lower():
                # 32-bit DLL / 64-bit Python mismatch — try the bridge
                _log("popstation.dll is 32-bit; attempting bridge via 32-bit Python…")
                ok = self._run_via_bridge(log_cb, total_size_cb, progress_cb)
                if ok:
                    return True
                # Bridge also failed — give a clear instruction
                self._error_msg = (
                    "popstation.dll requires 32-bit Python.\n"
                    "Install 32-bit Python 3.x from https://python.org/downloads/\n"
                    "then set the PYTHON32 environment variable:\n"
                    "  set PYTHON32=C:\\Python312-32\\python.exe\n"
                    "Alternatively, use the original PSX2PSP.exe for conversion."
                )
                _log(self._error_msg)
            else:
                self._error_msg = err
                _log(self._error_msg)
            return False
        except Exception as e:
            self._error_msg = str(e)
            _log(self._error_msg)
            return False

        # Set up message callback window
        cb = _ConvertCallback(total_size_cb, progress_cb, None)
        cb.start()

        info   = self._build_info(cb.hwnd)
        result = {"done": False, "success": False}

        def _on_done(ok: bool):
            result["done"]    = True
            result["success"] = ok
            self._done_event.set()
            cb.stop()

        cb._done_cb = _on_done

        _log(f"Starting conversion: {os.path.basename(self.iso_paths[0])} → "
             f"{os.path.basename(self.output_pbp)}")

        handle = dll.convert(info)
        if not handle:
            cb.stop()
            self._error_msg = "popstation.dll returned NULL handle."
            _log(self._error_msg)
            return False

        # Wait for completion (with timeout safety)
        self._done_event.wait(timeout=3600)   # 1-hour ceiling

        if result["success"]:
            _log(f"Conversion complete → {self.output_pbp}")
        else:
            _log("Conversion failed or was cancelled.")

        return result.get("success", False)


# ── PBP extraction ────────────────────────────────────────────────────────────

def extract_pbp(
    pbp_path:    str,
    output_iso:  str,
    log_cb:      Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> bool:
    """Extract the ISO from an EBOOT.PBP using popstation.dll."""

    def _log(msg):
        if log_cb:
            log_cb(msg)

    try:
        dll = _load_dll()
    except Exception as e:
        _log(str(e))
        return False

    cb = _ConvertCallback(None, progress_cb, None)
    cb.start()

    result = {"done": False, "success": False}
    done_ev = threading.Event()

    def _on_done(ok: bool):
        result["success"] = ok
        done_ev.set()
        cb.stop()

    cb._done_cb = _on_done

    info          = ExtractIsoInfo()
    info.callback = ctypes.c_void_p(cb.hwnd)
    info.srcPBP   = pbp_path.encode()[:MAX_PATH - 1] + b"\x00"
    info.dstISO   = output_iso.encode()[:MAX_PATH - 1] + b"\x00"

    _log(f"Extracting {pbp_path}…")
    handle = dll.extract(info)
    if not handle:
        cb.stop()
        _log("extract() returned NULL handle.")
        return False

    done_ev.wait(timeout=3600)
    return result.get("success", False)
