#!/usr/bin/env python
"""
popstation_bridge.py — 32-bit subprocess bridge for popstation.dll

Run this script with a 32-bit Python interpreter:
    C:\Python312-32\python.exe popstation_bridge.py

It reads a JSON job spec from stdin, calls popstation.dll via ctypes,
and writes progress + result back to stdout as JSON lines.

Stdin:  one JSON line with keys matching ConversionJob.__init__ params
Stdout: JSON lines: {"type": "log"|"progress"|"done", ...}

Usage from the main app (set PYTHON32 env var):
    set PYTHON32=C:\Python312-32\python.exe
"""
import sys
import os
import json
import ctypes
import struct
import threading

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_THIS_DIR, ".."))

POPSTATION_DLL = os.path.join(_ROOT_DIR, "Files", "popstation.dll")
BASE_PBP       = os.path.join(_ROOT_DIR, "Files", "BASE.PBP")
DATA_PSP       = os.path.join(_ROOT_DIR, "Files", "DATA.PSP")

MAX_PATH = 0xFF

def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def _log(msg: str):
    _emit({"type": "log", "msg": msg})

# ── ctypes structures ────────────────────────────────────────────────────────

class ConvertIsoInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("callback",   ctypes.c_void_p),
        ("base",       ctypes.c_char * MAX_PATH),
        ("data_psp",   ctypes.c_char * MAX_PATH),
        ("srcISO",     ctypes.c_char * MAX_PATH),
        ("dstPBP",     ctypes.c_char * MAX_PATH),
        ("pic0",       ctypes.c_char * MAX_PATH),
        ("pic1",       ctypes.c_char * MAX_PATH),
        ("icon0",      ctypes.c_char * MAX_PATH),
        ("icon1",      ctypes.c_char * MAX_PATH),
        ("snd0",       ctypes.c_char * MAX_PATH),
        ("boot",       ctypes.c_char * MAX_PATH),
        ("srcIsPbp",   ctypes.c_bool),
        ("gameTitle",  ctypes.c_char * MAX_PATH),
        ("saveTitle",  ctypes.c_char * MAX_PATH),
        ("gameID",     ctypes.c_char * MAX_PATH),
        ("saveID",     ctypes.c_char * MAX_PATH),
        ("compLevel",  ctypes.c_int),
        ("patchCount", ctypes.c_int),
        ("patchData",  ctypes.c_void_p),
    ]

# ── Callback (Win32 message pump) ────────────────────────────────────────────

_WM_USER    = 0x0400
_WM_DONE    = _WM_USER + 1
_WM_TOTAL   = _WM_USER + 2
_WM_PROG    = _WM_USER + 3
_WM_CANCEL  = _WM_USER + 4

class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   ctypes.c_void_p),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.c_void_p),
        ("hIcon",         ctypes.c_void_p),
        ("hCursor",       ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName",  ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",    ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam",  ctypes.c_size_t),
        ("lParam",  ctypes.c_ssize_t),
        ("time",    ctypes.c_uint),
        ("pt",      ctypes.c_long * 2),
    ]

user32 = ctypes.windll.user32
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                              ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)

_done_event  = threading.Event()
_result_ok   = False
_hwnd        = None

def _wndproc(hwnd, msg, wparam, lparam):
    global _result_ok
    if msg == _WM_DONE:
        _result_ok = bool(wparam)
        _done_event.set()
        return 0
    if msg == _WM_TOTAL:
        _emit({"type": "total", "value": wparam})
        return 0
    if msg == _WM_PROG:
        _emit({"type": "progress", "value": wparam})
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

_wndproc_cb = WNDPROC(_wndproc)

def _create_window():
    global _hwnd
    clsname  = "PSX2PSP_Bridge"
    wc       = WNDCLASS()
    wc.lpfnWndProc   = _wndproc_cb
    wc.lpszClassName = clsname
    user32.RegisterClassW(ctypes.byref(wc))
    _hwnd = user32.CreateWindowExW(0, clsname, "Bridge", 0,
                                    0, 0, 0, 0, None, None, None, None)

def _pump():
    """Run message loop until _done_event set."""
    msg = MSG()
    while not _done_event.is_set():
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

def _enc(s: str) -> bytes:
    return s.encode("utf-8") if s else b""

def run(spec: dict) -> bool:
    _create_window()
    _done_event.clear()

    dll = ctypes.cdll.LoadLibrary(POPSTATION_DLL)

    info            = ConvertIsoInfo()
    info.callback   = _hwnd
    info.base       = _enc(BASE_PBP)
    info.data_psp   = _enc(DATA_PSP)
    info.srcISO     = _enc(spec.get("iso_path", ""))
    info.dstPBP     = _enc(spec.get("output_pbp", ""))
    info.pic0       = _enc(spec.get("pic0_path", ""))
    info.pic1       = _enc(spec.get("pic1_path", ""))
    info.icon0      = _enc(spec.get("icon0_path", ""))
    info.icon1      = b""
    info.snd0       = _enc(spec.get("snd0_path", ""))
    info.boot       = _enc(spec.get("boot_path", ""))
    info.srcIsPbp   = False
    info.gameTitle  = _enc(spec.get("game_title", ""))
    info.saveTitle  = _enc(spec.get("save_title", ""))
    info.gameID     = _enc(spec.get("game_id", ""))
    info.saveID     = _enc(spec.get("save_id", ""))
    info.compLevel  = int(spec.get("comp_level", 9))
    info.patchCount = 0
    info.patchData  = None

    _log(f"Calling popstation.dll convert() …")
    threading.Thread(target=dll.convert, args=(ctypes.byref(info),), daemon=True).start()
    _pump()
    return _result_ok


if __name__ == "__main__":
    try:
        spec = json.loads(sys.stdin.readline())
        ok   = run(spec)
        _emit({"type": "done", "success": ok})
        sys.exit(0 if ok else 1)
    except Exception as e:
        _emit({"type": "error", "msg": str(e)})
        sys.exit(2)
