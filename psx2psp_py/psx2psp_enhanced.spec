# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PSX2PSP Enhanced.

Build command (run from the PSX2PSP root):
    pyinstaller psx2psp_py/psx2psp_enhanced.spec --distpath dist --workpath build/pyinstaller --clean --noconfirm

Output: dist/PSX2PSP_Enhanced.exe  (single-file, UPX compressed)
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Project root (one level above psx2psp_py/)
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

block_cipher = None

# Collect all of yt_dlp (its extractors are loaded dynamically)
ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all('yt_dlp')

# Collect Pillow data files (fonts, etc.)
pillow_datas = collect_data_files('PIL')

a = Analysis(
    [os.path.join(SPECPATH, 'psx2psp.py')],
    pathex=[SPECPATH],
    binaries=ytdlp_binaries,
    datas=[
        # Game database and support files
        (os.path.join(ROOT, 'Files', 'gameInfo.db'),  'Files'),
        (os.path.join(ROOT, 'Files', 'patches.ini'),  'Files'),
        (os.path.join(ROOT, 'Files', 'settings.ini'), 'Files'),
        (os.path.join(ROOT, 'Files', 'no_icon0.png'), 'Files'),
        (os.path.join(ROOT, 'Files', 'back.png'),     'Files'),
        # Python modules (include as data so relative imports work)
        (os.path.join(SPECPATH, 'modules'),           'modules'),
        # yt_dlp dynamic data
        *ytdlp_datas,
        # Pillow data (fonts, etc.)
        *pillow_datas,
    ],
    hiddenimports=[
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL.ImageFilter', 'PIL.ImageEnhance', 'PIL.ImageOps',
        'PIL.ImageTk', 'PIL._tkinter_finder',
        'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.extractor.lazy_extractors',
        'mutagen', 'mutagen.mp3', 'mutagen.id3',
        'tqdm', 'tqdm.auto',
        'requests', 'requests.adapters', 'requests.packages',
        'tkinter', 'tkinter.ttk', 'tkinter.filedialog',
        'tkinter.messagebox', 'tkinter.simpledialog',
        '_tkinter',
        *ytdlp_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy unused packages — saves ~60 MB
        'unittest', 'test', 'lib2to3',
        'xmlrpc', 'ftplib',
        'multiprocessing.managers', 'pydoc',
        'numpy', 'scipy', 'pandas', 'matplotlib',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'gi', 'cv2', 'torch', 'tensorflow',
    ],
    noarchive=False,
    optimize=2,          # strip docstrings + assert statements → smaller bytecode
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PSX2PSP_Enhanced',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,          # strip debug symbols from shared libs
    upx=True,            # UPX compress all PE sections
    upx_exclude=[
        # These break if UPX-compressed
        'vcruntime140.dll',
        'python3*.dll', 'python314.dll',
        '_tkinter*.pyd',
        'tcl*.dll', 'tk*.dll',
        'VCRUNTIME140.dll',
    ],
    runtime_tmpdir=None,
    console=False,       # no black console window (GUI-only)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
