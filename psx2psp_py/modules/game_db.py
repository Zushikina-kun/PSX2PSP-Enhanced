"""
game_db.py – Game ID detection from disc images + gameInfo.db lookup.

Handles:
  - Reading game serial from BIN/ISO (Mode1/Mode2 CD-ROM filesystem)
  - Reading CUE sheets to find the main binary track
  - Parsing gameInfo.db (semicolon-delimited CSV)
  - Normalising serial formats (SLUS_012.34 → SLUS01234)
"""
import os
import re
import struct
from functools import lru_cache
from typing import Optional, Dict, List, Tuple

from .constants import GAMEINFO_DB


# ─────────────────────────────────────────────────────────────────────────────
# Serial normalisation
# ─────────────────────────────────────────────────────────────────────────────

def normalise_serial(raw: str) -> str:
    """Convert any serial variant to the canonical SLUS01234 form."""
    # Remove spaces, dots, underscores, dashes
    s = re.sub(r"[\s._\-]", "", raw.upper())
    # Keep only alpha + digits
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def serial_to_db_key(serial: str) -> str:
    """
    Convert a normalised serial (SLUS01234) back to the hyphenated DB key
    format used in gameInfo.db  (SLUS-01234).
    Patterns: XXXX-NNNNN   or   XXXXX-NNNNN
    """
    m = re.match(r"^([A-Z]{2,5})(\d+)$", serial)
    if m:
        letters, digits = m.group(1), m.group(2)
        # Pad digits to 5 and insert dash
        digits = digits.zfill(5)
        return f"{letters}-{digits}"
    return serial


# ─────────────────────────────────────────────────────────────────────────────
# ISO / BIN reading helpers
# ─────────────────────────────────────────────────────────────────────────────

_SECTOR_SIZE_RAW  = 2352   # raw CD sector
_SECTOR_SIZE_M1   = 2048   # Mode1 data
_SECTOR_SYNC      = b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"

def _read_sector(f, lba: int, sector_size: int, data_offset: int = 0) -> bytes:
    """Read one logical sector of 2048 bytes from LBA."""
    f.seek(lba * sector_size + data_offset)
    raw = f.read(sector_size)
    return raw if raw else b""


def _detect_sector_params(f) -> Tuple[int, int]:
    """
    Returns (sector_size, data_offset) for the image.
    • Pure ISO (2048) → (2048, 0)
    • BIN RAW (2352)  → (2352, 24)   Mode2/Form1 header = 16+8
    • BIN Mode1 (2352)→ (2352, 16)
    """
    f.seek(0)
    header = f.read(16)
    if header[:12] == _SECTOR_SYNC:
        # Raw image – check Mode byte
        f.seek(15)
        mode = ord(f.read(1))
        if mode == 2:
            return 2352, 24    # Mode 2 Form 1 (PSX standard)
        return 2352, 16        # Mode 1
    return 2048, 0             # Plain ISO


def _read_pvd(f, sector_size: int, data_offset: int) -> Optional[bytes]:
    """Read the Primary Volume Descriptor at LBA 16."""
    return _read_sector(f, 16, sector_size, data_offset)


def _list_root_files(f, sector_size: int, data_offset: int) -> List[str]:
    """Return file names listed in the root directory of the ISO 9660 FS."""
    pvd = _read_pvd(f, sector_size, data_offset)
    if not pvd or len(pvd) < 2048:
        return []
    # Root directory record starts at byte 156 in PVD
    root_lba    = struct.unpack_from("<I", pvd, 158)[0]
    root_size   = struct.unpack_from("<I", pvd, 166)[0]
    root_data   = _read_sector(f, root_lba, sector_size, data_offset)
    names: List[str] = []
    offset = 0
    while offset < min(root_size, len(root_data)):
        rec_len = root_data[offset]
        if rec_len == 0:
            break
        name_len  = root_data[offset + 32]
        raw_name  = root_data[offset + 33: offset + 33 + name_len]
        name = raw_name.split(b";")[0].decode("ascii", errors="ignore").strip()
        if name and name not in (".", ".."):
            names.append(name)
        offset += rec_len
    return names


_SERIAL_RE = re.compile(
    r"\b(S[CKLP][A-Z]{2}[_\-]?\d{3}[._\-]?\d{2})\b", re.IGNORECASE
)


def detect_serial_from_image(path: str) -> Optional[str]:
    """
    Try to extract the PSX serial from a BIN or ISO file.
    Searches root directory for the game executable file (e.g. SLUS_012.34).
    Falls back to scanning the SYSTEM.CNF content.
    """
    try:
        with open(path, "rb") as f:
            ss, do = _detect_sector_params(f)
            # 1) Root directory file listing
            names = _list_root_files(f, ss, do)
            for name in names:
                m = _SERIAL_RE.search(name)
                if m:
                    return normalise_serial(m.group(1))
            # 2) Look for SYSTEM.CNF in root files and read it
            if "SYSTEM.CNF" in [n.upper() for n in names]:
                pvd      = _read_pvd(f, ss, do)
                root_lba = struct.unpack_from("<I", pvd, 158)[0] if pvd else 16
                root_sz  = struct.unpack_from("<I", pvd, 166)[0] if pvd else 2048
                root_dat = _read_sector(f, root_lba, ss, do)
                # Find SYSTEM.CNF entry
                offset = 0
                while offset < min(root_sz, len(root_dat)):
                    rec = root_dat[offset]
                    if rec == 0:
                        break
                    nl  = root_dat[offset + 32]
                    nm  = root_dat[offset + 33: offset + 33 + nl].split(b";")[0]
                    if nm.upper() == b"SYSTEM.CNF":
                        file_lba = struct.unpack_from("<I", root_dat, offset + 2)[0]
                        cnf_data = _read_sector(f, file_lba, ss, do)
                        cnf_text = cnf_data.decode("ascii", errors="ignore")
                        m2 = _SERIAL_RE.search(cnf_text)
                        if m2:
                            return normalise_serial(m2.group(1))
                        break
                    offset += rec
    except Exception:
        pass
    return None


def serial_from_cue(cue_path: str) -> Optional[str]:
    """
    Parse a CUE sheet, find the first FILE entry, then read that BIN for
    the serial.
    """
    try:
        cue_dir = os.path.dirname(cue_path)
        with open(cue_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r'\s*FILE\s+"?([^"]+)"?\s+BINARY', line, re.IGNORECASE)
                if not m:
                    m = re.match(r'\s*FILE\s+"?([^"]+)"?', line, re.IGNORECASE)
                if m:
                    bin_name = m.group(1).strip()
                    bin_path = os.path.join(cue_dir, bin_name)
                    if os.path.isfile(bin_path):
                        return detect_serial_from_image(bin_path)
    except Exception:
        pass
    return None


def detect_serial(path: str) -> Optional[str]:
    """
    Auto-detect serial from any supported path:
    .cue → find BIN → scan image
    .bin / .iso / .img → scan image directly
    Falls back to filename pattern matching.
    """
    ext = os.path.splitext(path)[1].lower()
    serial: Optional[str] = None

    if ext == ".cue":
        serial = serial_from_cue(path)
        if not serial:
            # Try same-name BIN
            bin_path = re.sub(r"\.cue$", ".bin", path, flags=re.IGNORECASE)
            if os.path.isfile(bin_path):
                serial = detect_serial_from_image(bin_path)
    elif ext in (".bin", ".iso", ".img"):
        serial = detect_serial_from_image(path)

    # Fallback: scan filename itself
    if not serial:
        m = _SERIAL_RE.search(os.path.basename(path))
        if m:
            serial = normalise_serial(m.group(1))

    return serial


# ─────────────────────────────────────────────────────────────────────────────
# gameInfo.db  loader
# ─────────────────────────────────────────────────────────────────────────────

class GameInfo:
    """Holds all fields from one gameInfo.db record."""
    __slots__ = ("game_id", "save_folder", "save_desc", "game_name",
                 "video_format", "scanner_id", "serial_norm")

    def __init__(self, game_id: str, save_folder: str, save_desc: str,
                 game_name: str, video_format: str, scanner_id: str):
        self.game_id      = game_id
        self.save_folder  = save_folder
        self.save_desc    = save_desc
        self.game_name    = game_name
        self.video_format = video_format   # NTSC / PAL
        self.scanner_id   = scanner_id
        self.serial_norm  = normalise_serial(game_id)

    def __repr__(self) -> str:
        return f"<GameInfo {self.game_id!r} {self.game_name!r}>"

    @property
    def is_ntsc(self) -> bool:
        return "NTSC" in self.video_format.upper()

    @property
    def is_pal(self) -> bool:
        return "PAL" in self.video_format.upper()

    # Convenience: generate the PSP save-game folder ID
    @property
    def save_id_formatted(self) -> str:
        """Returns e.g.  SLUS01234"""
        return self.save_folder.replace("-", "").replace("_", "")

    @property
    def game_id_formatted(self) -> str:
        """Hyphenated DB-style key, e.g. SLUS-01234"""
        return serial_to_db_key(self.serial_norm)


@lru_cache(maxsize=1)
def _load_db() -> Dict[str, GameInfo]:
    """Load gameInfo.db once and return a dict keyed by normalised serial."""
    db: Dict[str, GameInfo] = {}
    if not os.path.isfile(GAMEINFO_DB):
        return db
    with open(GAMEINFO_DB, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ";" not in line:
                continue
            parts = line.split(";")
            if len(parts) < 6:
                continue
            gi = GameInfo(*parts[:6])
            db[gi.serial_norm] = gi
            # Also index by raw game_id for direct lookup
            db[gi.game_id.upper()] = gi
    return db


def lookup_game(serial_or_id: str) -> Optional[GameInfo]:
    """
    Look up a game in gameInfo.db.
    Accepts any serial format (e.g. 'SLUS_012.34', 'SLUS01234', 'SLUS-01234').
    Returns GameInfo or None.
    """
    db = _load_db()
    # Try direct raw key
    upper = serial_or_id.upper()
    if upper in db:
        return db[upper]
    # Try normalised
    norm = normalise_serial(serial_or_id)
    if norm in db:
        return db[norm]
    # Try DB-key form
    dbkey = serial_to_db_key(norm)
    if dbkey in db:
        return db[dbkey]
    return None


def search_games(query: str, max_results: int = 20) -> List[GameInfo]:
    """Full-text search across game names in the DB (case-insensitive)."""
    db   = _load_db()
    q    = query.lower()
    seen = set()
    results: List[GameInfo] = []
    for gi in db.values():
        if id(gi) in seen:
            continue
        if q in gi.game_name.lower() or q in gi.game_id.lower():
            results.append(gi)
            seen.add(id(gi))
        if len(results) >= max_results:
            break
    results.sort(key=lambda g: g.game_name)
    return results


def db_size() -> int:
    """Return number of unique entries loaded from gameInfo.db."""
    db = _load_db()
    # Count unique GameInfo objects
    return len({id(v) for v in db.values()})
