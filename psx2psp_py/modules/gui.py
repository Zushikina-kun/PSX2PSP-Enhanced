"""
gui.py – Main PSX2PSP Python GUI (tkinter).

Tabs:
  1. Single Game  – load 1-4 discs, auto-fill info, convert
  2. Batch        – queue multiple games, convert all
  3. Settings     – compression, artwork/BGM toggles, output dir
  4. About        – tool info + dependency status
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, List
from PIL import Image, ImageTk

from .constants import (
    CLR_BG, CLR_PANEL, CLR_ACCENT, CLR_HILIGHT, CLR_FG, CLR_FG2,
    CLR_GREEN, CLR_ORANGE, CLR_RED,
    FONT_TITLE, FONT_LABEL, FONT_SMALL, FONT_MONO,
    OUTPUT_DIR, CACHE_DIR, DEFAULT_COMPRESS,
)
from .game_db  import detect_serial, lookup_game, search_games, db_size
from .batch    import GameSpec, BatchRunner, GameResult
from .bgm      import get_bgm_sources


# ── Cross-platform folder opener ──────────────────────────────────────────────

def _open_folder(path: str):
    """Open *path* in the system file manager."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", path])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path])


# ── Theme helpers ─────────────────────────────────────────────────────────────

def _apply_theme(root: tk.Tk):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".",
        background=CLR_BG, foreground=CLR_FG,
        fieldbackground=CLR_PANEL, troughcolor=CLR_PANEL,
        selectbackground=CLR_HILIGHT, selectforeground=CLR_FG,
        font=FONT_LABEL)
    style.configure("TFrame",   background=CLR_BG)
    style.configure("TLabel",   background=CLR_BG,   foreground=CLR_FG)
    style.configure("TButton",  background=CLR_ACCENT, foreground=CLR_FG,
                    padding=(8, 4))
    style.map("TButton",
        background=[("active", CLR_HILIGHT), ("pressed", CLR_HILIGHT)],
        foreground=[("active", "#ffffff")])
    style.configure("Accent.TButton", background=CLR_HILIGHT, foreground="#fff",
                    font=(*FONT_LABEL[:2], "bold"))
    style.map("Accent.TButton",
        background=[("active", "#c02040"), ("pressed", "#901030")])
    style.configure("TNotebook",        background=CLR_BG,    tabmargins=0)
    style.configure("TNotebook.Tab",    background=CLR_PANEL, foreground=CLR_FG2,
                    padding=(12, 5))
    style.map("TNotebook.Tab",
        background=[("selected", CLR_ACCENT)],
        foreground=[("selected", "#fff")])
    style.configure("TEntry",   fieldbackground=CLR_PANEL, foreground=CLR_FG,
                    insertcolor=CLR_FG)
    style.configure("TCombobox", fieldbackground=CLR_PANEL, foreground=CLR_FG,
                    selectbackground=CLR_ACCENT)
    style.configure("TCheckbutton", background=CLR_BG, foreground=CLR_FG)
    style.configure("TScrollbar", background=CLR_PANEL, troughcolor=CLR_BG,
                    arrowcolor=CLR_FG2)
    style.configure("Horizontal.TProgressbar", troughcolor=CLR_PANEL,
                    background=CLR_GREEN, thickness=14)
    style.configure("TLabelframe",        background=CLR_BG, foreground=CLR_FG2)
    style.configure("TLabelframe.Label",  background=CLR_BG, foreground=CLR_FG2,
                    font=FONT_SMALL)
    style.configure("TSpinbox",  fieldbackground=CLR_PANEL, foreground=CLR_FG)
    style.configure("Treeview",  background=CLR_PANEL, foreground=CLR_FG,
                    fieldbackground=CLR_PANEL, rowheight=22)
    style.configure("Treeview.Heading", background=CLR_ACCENT, foreground=CLR_FG)
    style.map("Treeview", background=[("selected", CLR_HILIGHT)])


def _lbl(parent, text, **kw) -> ttk.Label:
    return ttk.Label(parent, text=text, **kw)


def _btn(parent, text, cmd, style="TButton", **kw) -> ttk.Button:
    return ttk.Button(parent, text=text, command=cmd, style=style, **kw)


def _entry(parent, var, width=30, **kw) -> ttk.Entry:
    return ttk.Entry(parent, textvariable=var, width=width, **kw)


# ── Preview panel ─────────────────────────────────────────────────────────────

# ── Preview panel ─────────────────────────────────────────────────────────────

class PreviewPanel(ttk.Frame):
    """
    Multi-slot image preview: ICON0 / PIC0 / PIC1 / BOOT tabs
    plus animated ICON1 preview.
    """

    _SLOTS = ["ICON0 (144×80)", "PIC0 (480×272)", "PIC1 (480×272)", "BOOT (480×272)", "ICON1 anim"]
    # Display sizes for each slot (canvas dimensions)
    _CANVAS_W = 240
    _CANVAS_H = 136

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._imgs:      dict = {}    # slot → PIL Image
        self._tk_imgs:   dict = {}    # slot → ImageTk ref
        self._anim_frames: list = []
        self._anim_idx   = 0
        self._anim_job   = None
        self._cur_slot   = tk.StringVar(value=self._SLOTS[0])
        self._build()

    def _build(self):
        # Slot selector tabs (radio buttons styled as a tab bar)
        tab_f = ttk.Frame(self)
        tab_f.pack(fill="x")
        for slot in self._SLOTS:
            ttk.Radiobutton(
                tab_f, text=slot.split(" ")[0], variable=self._cur_slot,
                value=slot, style="TButton",
                command=self._on_slot_change
            ).pack(side="left", padx=1)

        # Canvas for current slot
        self._canvas = tk.Canvas(
            self, width=self._CANVAS_W, height=self._CANVAS_H,
            bg="#0a0a1a", highlightthickness=1,
            highlightbackground=CLR_ACCENT)
        self._canvas.pack(pady=(3, 2))
        self._canvas.create_text(
            self._CANVAS_W // 2, self._CANVAS_H // 2,
            text="No Image", fill=CLR_FG2, font=FONT_SMALL, tags="placeholder")

        # Info rows
        info_f = ttk.Frame(self)
        info_f.pack(fill="x", padx=4)

        self._var_title  = tk.StringVar(value="—")
        self._var_serial = tk.StringVar(value="—")
        self._var_region = tk.StringVar(value="—")
        self._var_status = tk.StringVar(value="")

        for lbl, var in (("Title:", self._var_title),
                         ("Serial:", self._var_serial),
                         ("Region:", self._var_region)):
            row = ttk.Frame(info_f)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=lbl, width=7, anchor="e",
                      foreground=CLR_FG2).pack(side="left")
            ttk.Label(row, textvariable=var, anchor="w",
                      wraplength=160).pack(side="left", fill="x", expand=True)

        self._status_lbl = ttk.Label(
            info_f, textvariable=self._var_status,
            foreground=CLR_GREEN, wraplength=200, font=FONT_SMALL)
        self._status_lbl.pack(pady=(4, 0))

    # ── public setters ────────────────────────────────────────────────────────

    def set_image(self, img: Optional[Image.Image], slot: str = "ICON0"):
        """Store image for *slot* (ICON0/PIC0/PIC1/BOOT) and refresh if active."""
        self._imgs[slot] = img
        tab_key = next((s for s in self._SLOTS if s.startswith(slot)), self._SLOTS[0])
        if self._cur_slot.get() == tab_key:
            self._render(img)

    def set_animation_frames(self, frames: list):
        """Store ICON1 animation frames and start playback if that slot is active."""
        self._anim_frames = frames
        if self._cur_slot.get() == self._SLOTS[4]:
            self._start_anim()

    def set_info(self, title="—", serial="—", region="—", status=""):
        self._var_title.set(title[:48])
        self._var_serial.set(serial)
        self._var_region.set(region)
        self._var_status.set(status)
        colour = (CLR_GREEN  if "done"  in status.lower() or "ready" in status.lower() else
                  CLR_RED    if "fail"  in status.lower() else
                  CLR_ORANGE if status else CLR_FG2)
        self._status_lbl.configure(foreground=colour)

    # ── internal ─────────────────────────────────────────────────────────────

    def _on_slot_change(self):
        self._stop_anim()
        slot_label = self._cur_slot.get()
        if slot_label == self._SLOTS[4]:          # ICON1 anim
            self._start_anim()
            return
        slot_key = slot_label.split(" ")[0]       # e.g. "ICON0"
        self._render(self._imgs.get(slot_key))

    def _render(self, img: Optional[Image.Image]):
        self._canvas.delete("all")
        if img is None:
            self._canvas.create_text(
                self._CANVAS_W // 2, self._CANVAS_H // 2,
                text="No Image", fill=CLR_FG2, font=FONT_SMALL)
            return
        # Scale to fit canvas preserving aspect ratio
        iw, ih = img.size
        scale  = min(self._CANVAS_W / iw, self._CANVAS_H / ih)
        nw     = max(1, int(iw * scale))
        nh     = max(1, int(ih * scale))
        thumb  = img.resize((nw, nh), Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(thumb)
        self._tk_imgs["cur"] = tk_img   # keep ref
        ox = (self._CANVAS_W - nw) // 2
        oy = (self._CANVAS_H - nh) // 2
        self._canvas.create_image(ox, oy, anchor="nw", image=tk_img)

    def _start_anim(self):
        if not self._anim_frames:
            self._canvas.delete("all")
            self._canvas.create_text(
                self._CANVAS_W // 2, self._CANVAS_H // 2,
                text="No animation", fill=CLR_FG2, font=FONT_SMALL)
            return
        self._anim_idx = 0
        self._tick_anim()

    def _tick_anim(self):
        if not self._anim_frames or self._cur_slot.get() != self._SLOTS[4]:
            return
        frame = self._anim_frames[self._anim_idx % len(self._anim_frames)]
        self._render(frame)
        self._anim_idx += 1
        self._anim_job = self.after(80, self._tick_anim)

    def _stop_anim(self):
        if self._anim_job:
            self.after_cancel(self._anim_job)
            self._anim_job = None


# ── Log console widget ────────────────────────────────────────────────────────

class LogConsole(ttk.Frame):
    def __init__(self, parent, height=8, **kw):
        super().__init__(parent, **kw)
        self._txt = tk.Text(self, height=height, bg="#0d0d1a", fg=CLR_FG,
                             font=FONT_MONO, state="disabled",
                             wrap="word", relief="flat")
        sb = ttk.Scrollbar(self, command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb.set)
        self._txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # Colour tags
        self._txt.tag_configure("ok",    foreground=CLR_GREEN)
        self._txt.tag_configure("warn",  foreground=CLR_ORANGE)
        self._txt.tag_configure("err",   foreground=CLR_RED)
        self._txt.tag_configure("info",  foreground=CLR_FG)

    def append(self, msg: str):
        tag = ("err"  if any(x in msg.lower() for x in ("error","fail","cannot")) else
               "warn" if any(x in msg.lower() for x in ("warn","skip","not found")) else
               "ok"   if any(x in msg.lower() for x in ("done","complete","found","ready","created")) else
               "info")
        self._txt.configure(state="normal")
        self._txt.insert("end", msg + "\n", tag)
        self._txt.see("end")
        self._txt.configure(state="disabled")

    def clear(self):
        self._txt.configure(state="normal")
        self._txt.delete("1.0", "end")
        self._txt.configure(state="disabled")


# ── Single-game tab ───────────────────────────────────────────────────────────

class SingleGameTab(ttk.Frame):
    def __init__(self, parent, settings: dict, **kw):
        super().__init__(parent, **kw)
        self._settings   = settings
        self._disc_paths: List[str] = []
        self._runner:     Optional[BatchRunner] = None
        self._artwork     = None   # ArtworkBundle reference
        self._build()

    def _build(self):
        # ── Left: disc list + fields ──────────────────────────────────────────
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        # Disc selection
        disc_f = ttk.LabelFrame(left, text=" Disc Images (1–4) ")
        disc_f.pack(fill="x", pady=(0, 6))

        self._disc_list = tk.Listbox(disc_f, height=4, bg=CLR_PANEL,
                                      fg=CLR_FG, font=FONT_SMALL,
                                      selectbackground=CLR_HILIGHT,
                                      relief="flat", activestyle="none")
        self._disc_list.pack(fill="x", padx=4, pady=4)

        btn_row = ttk.Frame(disc_f)
        btn_row.pack(fill="x", padx=4, pady=(0, 4))
        _btn(btn_row, "➕ Add Disc",    self._add_disc).pack(side="left", padx=2)
        _btn(btn_row, "✖ Remove",       self._remove_disc).pack(side="left", padx=2)
        _btn(btn_row, "🔍 Auto-Detect", self._auto_detect).pack(side="left", padx=2)

        # Game info fields
        info_f = ttk.LabelFrame(left, text=" Game Information ")
        info_f.pack(fill="x", pady=(0, 6))

        self._v_title   = tk.StringVar()
        self._v_stitle  = tk.StringVar()
        self._v_gid     = tk.StringVar()
        self._v_sid     = tk.StringVar()
        self._v_serial  = tk.StringVar()
        self._v_region  = tk.StringVar(value="NTSC")

        fields = [
            ("Game Title:",  self._v_title,  36),
            ("Save Title:",  self._v_stitle, 36),
            ("Game ID:",     self._v_gid,    20),
            ("Save ID:",     self._v_sid,    20),
            ("Serial:",      self._v_serial, 16),
        ]
        for row_idx, (lbl, var, w) in enumerate(fields):
            ttk.Label(info_f, text=lbl, width=11, anchor="e"
                      ).grid(row=row_idx, column=0, sticky="e", padx=4, pady=2)
            _entry(info_f, var, width=w).grid(row=row_idx, column=1,
                                               sticky="w", padx=4, pady=2)

        # Region
        ttk.Label(info_f, text="Region:", width=11, anchor="e"
                  ).grid(row=len(fields), column=0, sticky="e", padx=4, pady=2)
        region_cb = ttk.Combobox(info_f, textvariable=self._v_region,
                                  values=["NTSC", "PAL"], width=8, state="readonly")
        region_cb.grid(row=len(fields), column=1, sticky="w", padx=4, pady=2)

        # Asset overrides
        asset_f = ttk.LabelFrame(left, text=" Asset Overrides (optional) ")
        asset_f.pack(fill="x", pady=(0, 6))

        self._v_icon0 = tk.StringVar()
        self._v_pic1  = tk.StringVar()
        self._v_snd0  = tk.StringVar()
        self._v_shot  = tk.StringVar()   # custom screenshot for PIC1/BOOT
        self._v_out   = tk.StringVar(value=OUTPUT_DIR)

        asset_rows = [
            ("ICON0 (cover):",  self._v_icon0, "image",
             lambda: self._browse_file(self._v_icon0,
                     [("Images","*.png *.jpg *.jpeg *.bmp")])),
            ("Screenshot:",     self._v_shot,  "image",
             lambda: self._browse_file(self._v_shot,
                     [("Images","*.png *.jpg *.jpeg *.bmp")])),
            ("PIC1 (bg):",      self._v_pic1,  "image",
             lambda: self._browse_file(self._v_pic1,
                     [("Images","*.png *.jpg *.jpeg *.bmp")])),
            ("SND0 (audio):",   self._v_snd0,  "audio",
             lambda: self._browse_file(self._v_snd0,
                     [("Audio","*.mp3 *.wav *.ogg *.flac *.at3")])),
            ("Output dir:",     self._v_out,   "dir",
             lambda: self._browse_dir(self._v_out)),
        ]
        for r, (lbl, var, kind, cmd) in enumerate(asset_rows):
            ttk.Label(asset_f, text=lbl, width=14, anchor="e"
                      ).grid(row=r, column=0, sticky="e", padx=4, pady=2)
            _entry(asset_f, var, width=28
                   ).grid(row=r, column=1, sticky="ew", padx=4, pady=2)
            _btn(asset_f, "…", cmd, width=3
                 ).grid(row=r, column=2, padx=2, pady=2)
        asset_f.columnconfigure(1, weight=1)

        # ── Right: preview + progress ─────────────────────────────────────────
        right = ttk.Frame(self)
        right.pack(side="right", fill="y", padx=8, pady=8)

        self._preview = PreviewPanel(right)
        self._preview.pack(pady=(0, 8))

        # Artwork fetch button
        _btn(right, "🎨 Fetch Artwork",
             self._fetch_artwork_only).pack(fill="x", pady=2)
        _btn(right, "🎵 Fetch BGM",
             self._fetch_bgm_only).pack(fill="x", pady=2)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        self._prog_var = tk.DoubleVar(value=0)
        self._prog_lbl = tk.StringVar(value="Ready")
        ttk.Label(right, textvariable=self._prog_lbl,
                  font=FONT_SMALL, foreground=CLR_FG2).pack()
        ttk.Progressbar(right, variable=self._prog_var,
                        maximum=100, length=190,
                        style="Horizontal.TProgressbar").pack(pady=4)

        _btn(right, "▶  CONVERT", self._start_convert,
             style="Accent.TButton").pack(fill="x", pady=4)
        _btn(right, "⏹  Cancel",  self._cancel).pack(fill="x")

        # ── Log at bottom ─────────────────────────────────────────────────────
        log_f = ttk.LabelFrame(self, text=" Log ")
        log_f.pack(fill="both", expand=True, padx=8, pady=(0, 8),
                   side="bottom")
        self._log = LogConsole(log_f, height=7)
        self._log.pack(fill="both", expand=True, padx=2, pady=2)

    # ── disc management ───────────────────────────────────────────────────────

    def _add_disc(self):
        if len(self._disc_paths) >= 4:
            messagebox.showwarning("Limit", "Maximum 4 discs supported.")
            return
        paths = filedialog.askopenfilenames(
            title="Select Disc Image(s)",
            filetypes=[("Disc Images","*.bin *.iso *.img *.cue"),
                       ("All Files","*.*")])
        for p in paths:
            if p not in self._disc_paths and len(self._disc_paths) < 4:
                self._disc_paths.append(p)
                self._disc_list.insert("end", os.path.basename(p))

    def _remove_disc(self):
        sel = self._disc_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self._disc_list.delete(idx)
        self._disc_paths.pop(idx)

    def _browse_file(self, var: tk.StringVar, ftypes):
        p = filedialog.askopenfilename(filetypes=ftypes)
        if p:
            var.set(p)

    def _browse_dir(self, var: tk.StringVar):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    # ── auto-detect ───────────────────────────────────────────────────────────

    def _auto_detect(self):
        if not self._disc_paths:
            messagebox.showinfo("Auto-Detect", "Add a disc image first.")
            return
        self._log.clear()
        self._log.append("Auto-detecting serial…")
        serial = detect_serial(self._disc_paths[0])
        if not serial:
            self._log.append("WARNING: Serial not detected from image.")
            return
        self._v_serial.set(serial)
        self._log.append(f"Serial: {serial}")
        gi = lookup_game(serial)
        if gi:
            self._v_title.set(gi.game_name)
            self._v_stitle.set(gi.save_desc or gi.game_name)
            self._v_gid.set(gi.game_id_formatted)
            self._v_sid.set(gi.save_folder)
            self._v_region.set(gi.video_format)
            self._log.append(f"Found: {gi.game_name} ({gi.video_format})")
            self._preview.set_info(gi.game_name, serial, gi.video_format)
        else:
            self._log.append("Not found in gameInfo.db.")
            self._preview.set_info(serial, serial, "?")

    # ── artwork / bgm helpers ─────────────────────────────────────────────────

    def _fetch_artwork_only(self):
        serial = self._v_serial.get().strip() or "UNKN00000"
        title  = self._v_title.get().strip()  or serial
        self._log.append(f"Fetching artwork for '{title}'…")

        def _work():
            from .artwork import (build_artwork_bundle, search_cover_candidates,
                                   search_screenshot_candidates)
            from .game_db import normalise_serial
            norm = normalise_serial(serial)

            # Collect all cover candidates
            self._safe_log("Searching cover sources…")
            cover_cands = search_cover_candidates(norm, title,
                                                   progress_cb=lambda m: self._safe_log(m))
            # Collect screenshot candidates
            self._safe_log("Searching screenshot sources…")
            shot_cands  = search_screenshot_candidates(norm, title,
                                                        progress_cb=lambda m: self._safe_log(m))

            chosen_cover = [None]
            chosen_shot  = [None]
            done_event   = threading.Event()

            def _after_cover_pick(cand):
                chosen_cover[0] = cand
                if shot_cands:
                    self.after(0, lambda: self._show_image_picker(
                        shot_cands, "Select Background Screenshot",
                        lambda c: (_assign_shot(c), done_event.set())))
                else:
                    done_event.set()

            def _assign_shot(c):
                chosen_shot[0] = c

            def _skip_cover():
                if shot_cands:
                    self.after(0, lambda: self._show_image_picker(
                        shot_cands, "Select Background Screenshot",
                        lambda c: (_assign_shot(c), done_event.set())))
                else:
                    done_event.set()

            if cover_cands:
                self.after(0, lambda: self._show_image_picker(
                    cover_cands, "Select Cover Image",
                    lambda c: (_after_cover_pick(c))))
            else:
                _skip_cover()

            done_event.wait(timeout=300)

            bundle = build_artwork_bundle(
                serial            = norm,
                game_name         = title,
                progress_cb       = lambda m: self._safe_log(m),
                custom_cover_path = self._v_icon0.get() or None,
                custom_screenshot_path = self._v_shot.get() or None,
                chosen_cover      = chosen_cover[0],
                chosen_screenshot = chosen_shot[0],
                fetch_screenshot  = True,
                animate_icon      = True,
            )
            self._artwork = bundle
            for slot, img in [("ICON0", bundle.icon0), ("PIC0", bundle.pic0),
                               ("PIC1", bundle.pic1),  ("BOOT", bundle.boot)]:
                if img:
                    self.after(0, lambda s=slot, i=img: self._preview.set_image(i, s))
            if bundle.icon1_frames:
                self.after(0, lambda: self._preview.set_animation_frames(bundle.icon1_frames))
            self.after(0, lambda: self._preview.set_info(
                title, serial, self._v_region.get(), "Artwork ready."))

        threading.Thread(target=_work, daemon=True).start()

    def _show_image_picker(self, candidates: list, title: str, result_cb):
        """Open ImagePickDialog on the main thread."""
        ImagePickDialog(self, candidates, title, result_cb)

    def _fetch_bgm_only(self):
        serial       = self._v_serial.get().strip() or "UNKN00000"
        raw_title    = self._v_title.get().strip()  or serial
        snd_override = self._v_snd0.get().strip()
        sources = self._settings.get("bgm_sources", ["khinsider", "archive", "youtube"])
        from .bgm import clean_title as _ct
        title = _ct(raw_title)
        self._log.append(f"Searching BGM for '{title}' (sources: {', '.join(sources)})…")
        tmp_at3 = os.path.join(CACHE_DIR, f"bgm_{serial}_preview.at3")

        def _work():
            from .bgm import search_bgm, download_bgm_candidate, convert_to_at3
            from .game_db import normalise_serial
            norm = normalise_serial(serial)

            if snd_override and os.path.isfile(snd_override):
                ok = convert_to_at3(snd_override, tmp_at3,
                                    progress_cb=lambda m: self._safe_log(m))
                if ok:
                    self._v_snd0.set(tmp_at3)
                    self._safe_log(f"Custom audio converted: {os.path.basename(tmp_at3)}")
                return

            candidates = search_bgm(title, sources, lambda m: self._safe_log(m))
            if not candidates:
                self._safe_log("No BGM candidates found.")
                return

            done_event = threading.Event()
            chosen     = [None]
            track_idx  = [0]

            def _on_pick(cand, tidx=0):
                chosen[0]    = cand
                track_idx[0] = tidx
                done_event.set()

            def _show_picker():
                BgmPickDialog(self, candidates, _on_pick)

            self.after(0, _show_picker)
            done_event.wait(timeout=300)

            if chosen[0] is None:
                self._safe_log("BGM selection cancelled.")
                return

            ok = download_bgm_candidate(
                chosen[0], tmp_at3,
                loop       = True,
                progress_cb= lambda m: self._safe_log(m),
                track_index= track_idx[0],
            )
            if ok:
                self._v_snd0.set(tmp_at3)
                sz = os.path.getsize(tmp_at3) // 1024
                self._safe_log(f"BGM ready: {os.path.basename(tmp_at3)} ({sz} KB)")
            else:
                self._safe_log("BGM download/conversion failed.")

        threading.Thread(target=_work, daemon=True).start()

    def _safe_log(self, msg: str):
        self.after(0, lambda: self._log.append(msg))

    def _set_progress(self, msg: str, done: int, total: int):
        self._prog_lbl.set(msg[:50])
        if total > 0:
            pct = min(100.0, done / total * 100)
            self._prog_var.set(pct)

    # ── conversion ────────────────────────────────────────────────────────────

    def _build_spec(self) -> Optional[GameSpec]:
        if not self._disc_paths:
            messagebox.showerror("Error", "Add at least one disc image.")
            return None
        s = self._settings
        spec = GameSpec(
            disc_paths     = list(self._disc_paths),
            serial         = self._v_serial.get().strip(),
            game_title     = self._v_title.get().strip(),
            save_title     = self._v_stitle.get().strip(),
            game_id        = self._v_gid.get().strip(),
            save_id        = self._v_sid.get().strip(),
            custom_icon0   = self._v_icon0.get().strip(),
            custom_pic1    = self._v_pic1.get().strip(),
            custom_snd0    = self._v_snd0.get().strip(),
            output_dir     = self._v_out.get().strip() or OUTPUT_DIR,
            comp_level     = int(s.get("comp_level", DEFAULT_COMPRESS)),
            apply_patches  = bool(s.get("apply_patches", False)),
            fetch_artwork  = bool(s.get("fetch_artwork", True)),
            fetch_bgm      = bool(s.get("fetch_bgm", True)),
            bgm_sources    = s.get("bgm_sources", ["khinsider", "archive", "youtube"]),
        )
        return spec

    def _start_convert(self):
        if self._runner and self._runner.is_running:
            messagebox.showinfo("Busy", "Conversion already running.")
            return
        spec = self._build_spec()
        if spec is None:
            return
        self._log.clear()
        self._log.append("Starting conversion…")
        self._prog_var.set(0)
        self._prog_lbl.set("Starting…")

        def _on_progress(msg, done, total):
            self.after(0, lambda: self._set_progress(msg, done, total))
            self.after(0, lambda: self._log.append(msg))

        def _on_done(result: GameResult):
            if result.success:
                msg = f"✅ Done: {os.path.basename(result.pbp_path)}"
                self.after(0, lambda: self._prog_var.set(100))
                self.after(0, lambda: self._prog_lbl.set("Complete!"))
                self.after(0, lambda: self._preview.set_info(
                    spec.game_title, spec.serial, spec.video_format, "Done!"))
                self.after(0, lambda: messagebox.showinfo(
                    "Conversion Complete",
                    f"EBOOT.PBP created:\n{result.pbp_path}"))
            else:
                msg = f"❌ Failed: {result.error_msg}"
                self.after(0, lambda: self._prog_lbl.set("Failed"))
                self.after(0, lambda: self._preview.set_info(
                    spec.game_title, spec.serial, spec.video_format, "Failed!"))
            self.after(0, lambda: self._log.append(msg))

        self._runner = BatchRunner(
            [spec],
            job_progress_cb = _on_progress,
            job_done_cb     = _on_done,
        )
        self._runner.start_async()

    def _cancel(self):
        if self._runner:
            self._runner.cancel()
            self._log.append("Cancelling…")
            self._prog_lbl.set("Cancelled")


# ── Batch tab ─────────────────────────────────────────────────────────────────

class BatchTab(ttk.Frame):
    def __init__(self, parent, settings: dict, **kw):
        super().__init__(parent, **kw)
        self._settings = settings
        self._runner:  Optional[BatchRunner] = None
        self._specs:   List[GameSpec] = []
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=8, pady=8)

        # Queue treeview
        cols = ("disc", "title", "serial", "status")
        self._tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        for col, hdr, w in zip(cols,
                                ("Disc Image", "Title", "Serial", "Status"),
                                (280, 200, 100, 100)):
            self._tree.heading(col, text=hdr)
            self._tree.column(col, width=w, minwidth=60)
        vsb = ttk.Scrollbar(top, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        # Control panel
        ctrl = ttk.Frame(top)
        ctrl.pack(side="right", fill="y", padx=(8, 0))

        _btn(ctrl, "➕ Add Games",    self._add_games).pack(fill="x", pady=2)
        _btn(ctrl, "➕ Add Folder",   self._add_folder).pack(fill="x", pady=2)
        _btn(ctrl, "✖  Remove",       self._remove_selected).pack(fill="x", pady=2)
        _btn(ctrl, "🗑  Clear All",    self._clear_all).pack(fill="x", pady=2)
        ttk.Separator(ctrl, orient="horizontal").pack(fill="x", pady=6)
        _btn(ctrl, "🔍 Auto-Fill All", self._autofill_all).pack(fill="x", pady=2)

        ttk.Separator(ctrl, orient="horizontal").pack(fill="x", pady=6)
        self._prog_lbl = tk.StringVar(value="0 / 0")
        ttk.Label(ctrl, textvariable=self._prog_lbl,
                  font=FONT_SMALL, foreground=CLR_FG2).pack()
        self._prog_bar = ttk.Progressbar(ctrl, length=160, maximum=100,
                                          style="Horizontal.TProgressbar")
        self._prog_bar.pack(pady=4)
        _btn(ctrl, "▶  Convert All", self._start_all,
             style="Accent.TButton").pack(fill="x", pady=4)
        _btn(ctrl, "⏹  Cancel",      self._cancel).pack(fill="x")

        # Log
        log_f = ttk.LabelFrame(self, text=" Log ")
        log_f.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self._log = LogConsole(log_f, height=5)
        self._log.pack(fill="both", expand=True, padx=2, pady=2)

    # ── queue management ──────────────────────────────────────────────────────

    def _add_games(self):
        paths = filedialog.askopenfilenames(
            title="Select Disc Images",
            filetypes=[("Disc Images","*.bin *.iso *.img *.cue"),
                       ("All Files","*.*")])
        for p in paths:
            self._add_path(p)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder with Disc Images")
        if not folder:
            return
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith((".bin", ".iso", ".img", ".cue")):
                self._add_path(os.path.join(folder, fname))

    def _add_path(self, path: str):
        spec = GameSpec(disc_paths=[path])
        self._specs.append(spec)
        iid = self._tree.insert("", "end",
            values=(os.path.basename(path), "—", "—", "Queued"))
        spec._tree_iid = iid  # type: ignore

    def _remove_selected(self):
        for iid in self._tree.selection():
            idx = self._tree.index(iid)
            self._tree.delete(iid)
            if idx < len(self._specs):
                self._specs.pop(idx)

    def _clear_all(self):
        self._tree.delete(*self._tree.get_children())
        self._specs.clear()

    def _autofill_all(self):
        self._log.append("Auto-filling all entries…")
        def _work():
            for spec in self._specs:
                if not spec.disc_paths:
                    continue
                serial = detect_serial(spec.disc_paths[0])
                if serial:
                    spec.serial = serial
                    gi = lookup_game(serial)
                    if gi:
                        spec.game_title   = gi.game_name
                        spec.save_title   = gi.save_desc or gi.game_name
                        spec.game_id      = gi.game_id_formatted
                        spec.save_id      = gi.save_folder
                        spec.video_format = gi.video_format
                    self._update_row(spec, serial,
                                     spec.game_title or "?", "Ready")
        threading.Thread(target=_work, daemon=True).start()

    def _update_row(self, spec, serial, title, status):
        iid = getattr(spec, "_tree_iid", None)
        if iid:
            self.after(0, lambda: self._tree.item(iid, values=(
                os.path.basename(spec.disc_paths[0]), title, serial, status)))

    # ── batch conversion ──────────────────────────────────────────────────────

    def _start_all(self):
        if not self._specs:
            messagebox.showinfo("Empty", "No games in queue.")
            return
        if self._runner and self._runner.is_running:
            messagebox.showinfo("Busy", "Already running.")
            return
        s = self._settings
        for spec in self._specs:
            spec.output_dir    = s.get("output_dir", OUTPUT_DIR) or OUTPUT_DIR
            spec.comp_level    = int(s.get("comp_level", DEFAULT_COMPRESS))
            spec.fetch_artwork = bool(s.get("fetch_artwork", True))
            spec.fetch_bgm     = bool(s.get("fetch_bgm", True))

        self._log.clear()
        total = len(self._specs)
        done  = [0]

        def _on_progress(msg, b_done, b_total):
            self.after(0, lambda: self._log.append(msg))

        def _on_done(result: GameResult):
            done[0] += 1
            pct = done[0] / total * 100
            status = "✅ Done" if result.success else "❌ Failed"
            self._update_row(result.spec, result.spec.serial,
                             result.spec.game_title, status)
            self.after(0, lambda: self._prog_bar.configure(value=pct))
            self.after(0, lambda: self._prog_lbl.set(f"{done[0]} / {total}"))

        def _all_done(results):
            ok  = sum(1 for r in results if r.success)
            msg = f"Batch complete: {ok}/{total} succeeded."
            self.after(0, lambda: self._log.append(msg))
            self.after(0, lambda: messagebox.showinfo("Batch Done", msg))

        self._runner = BatchRunner(
            list(self._specs),
            job_progress_cb = _on_progress,
            job_done_cb     = _on_done,
            all_done_cb     = _all_done,
        )
        self._runner.start_async()

    def _cancel(self):
        if self._runner:
            self._runner.cancel()
            self._log.append("Cancelling batch…")


# ── Settings tab ──────────────────────────────────────────────────────────────

class SettingsTab(ttk.Frame):
    def __init__(self, parent, settings: dict, **kw):
        super().__init__(parent, **kw)
        self._s = settings
        self._build()

    def _build(self):
        pad = {"padx": 12, "pady": 4}

        # ── Conversion ────────────────────────────────────────────────────────
        conv_f = ttk.LabelFrame(self, text=" Conversion ")
        conv_f.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(conv_f, text="Compression level (0=none, 9=max):").grid(
            row=0, column=0, sticky="w", **pad)
        self._v_comp = tk.IntVar(value=self._s.get("comp_level", DEFAULT_COMPRESS))
        comp_spin = ttk.Spinbox(conv_f, from_=0, to=9,
                                textvariable=self._v_comp, width=4)
        comp_spin.grid(row=0, column=1, sticky="w", **pad)

        self._v_patches = tk.BooleanVar(value=self._s.get("apply_patches", False))
        ttk.Checkbutton(conv_f, text="Apply PAL→NTSC patches automatically",
                        variable=self._v_patches).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)

        # ── Artwork ───────────────────────────────────────────────────────────
        art_f = ttk.LabelFrame(self, text=" Artwork ")
        art_f.pack(fill="x", padx=10, pady=4)

        self._v_fetch_art = tk.BooleanVar(value=self._s.get("fetch_artwork", True))
        ttk.Checkbutton(art_f, text="Auto-fetch cover art from internet",
                        variable=self._v_fetch_art).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)

        self._v_fetch_shot = tk.BooleanVar(value=self._s.get("fetch_screenshot", True))
        ttk.Checkbutton(art_f, text="Fetch in-game screenshot for background (PIC1/BOOT)",
                        variable=self._v_fetch_shot).grid(
            row=1, column=0, columnspan=3, sticky="w", **pad)

        self._v_animate_icon = tk.BooleanVar(value=self._s.get("animate_icon", True))
        ttk.Checkbutton(art_f, text="Generate ICON1 animation frames (Ken-Burns effect)",
                        variable=self._v_animate_icon).grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(art_f, text="TheGamesDB API key (optional):").grid(
            row=3, column=0, sticky="w", **pad)
        self._v_tgdb_key = tk.StringVar(
            value=self._s.get("tgdb_api_key", ""))
        ttk.Entry(art_f, textvariable=self._v_tgdb_key, width=28,
                  show="").grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(art_f, text="(enables TheGamesDB covers & screenshots)",
                  foreground=CLR_FG2, font=FONT_SMALL).grid(
            row=4, column=1, columnspan=2, sticky="w", padx=12)
        art_f.columnconfigure(1, weight=1)

        # ── BGM ───────────────────────────────────────────────────────────────
        bgm_f = ttk.LabelFrame(self, text=" BGM / Sound ")
        bgm_f.pack(fill="x", padx=10, pady=4)

        self._v_fetch_bgm = tk.BooleanVar(value=self._s.get("fetch_bgm", True))
        ttk.Checkbutton(bgm_f, text="Auto-search & download BGM",
                        variable=self._v_fetch_bgm).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad)

        self._v_loop_bgm = tk.BooleanVar(value=self._s.get("loop_bgm", True))
        ttk.Checkbutton(bgm_f, text="Loop BGM (-wholeloop flag in AT3)",
                        variable=self._v_loop_bgm).grid(
            row=1, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(bgm_f, text="AT3 bitrate (kbps):").grid(
            row=2, column=0, sticky="w", **pad)
        self._v_at3_br = tk.IntVar(value=self._s.get("at3_bitrate", 132))
        ttk.Combobox(bgm_f, textvariable=self._v_at3_br,
                     values=[66, 132], width=6, state="readonly").grid(
            row=2, column=1, sticky="w", **pad)

        # Source order checkboxes
        ttk.Label(bgm_f, text="Search sources (drag to reorder — check to enable):",
                  foreground=CLR_FG2, font=FONT_SMALL).grid(
            row=3, column=0, columnspan=4, sticky="w", **pad)

        saved_sources = self._s.get("bgm_sources", ["khinsider", "archive", "youtube"])
        self._bgm_src_vars = {}
        src_labels = {
            "khinsider": "KH Insider (downloads.khinsider.com)",
            "archive":   "Internet Archive (archive.org)",
            "youtube":   "YouTube via yt-dlp",
        }
        src_frame = ttk.Frame(bgm_f)
        src_frame.grid(row=4, column=0, columnspan=4, sticky="w", padx=12)
        for i, (key, label) in enumerate(src_labels.items()):
            var = tk.BooleanVar(value=key in saved_sources)
            self._bgm_src_vars[key] = var
            ttk.Checkbutton(src_frame, text=label, variable=var).grid(
                row=i, column=0, sticky="w", pady=1)

        # BGM tool status row
        srcs = get_bgm_sources()
        status_f = ttk.Frame(bgm_f)
        status_f.grid(row=5, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(status_f, text="Tools:", foreground=CLR_FG2,
                  font=FONT_SMALL).pack(side="left", padx=(0, 4))
        for tool, avail in srcs.items():
            colour = CLR_GREEN if avail else CLR_RED
            mark   = "✔" if avail else "✘"
            ttk.Label(status_f, text=f"{mark} {tool}",
                      foreground=colour, font=FONT_SMALL).pack(
                side="left", padx=5)

        # ── Output ────────────────────────────────────────────────────────────
        out_f = ttk.LabelFrame(self, text=" Output ")
        out_f.pack(fill="x", padx=10, pady=4)

        ttk.Label(out_f, text="Default output directory:").grid(
            row=0, column=0, sticky="w", **pad)
        self._v_outdir = tk.StringVar(
            value=self._s.get("output_dir", OUTPUT_DIR))
        ttk.Entry(out_f, textvariable=self._v_outdir, width=36).grid(
            row=0, column=1, sticky="ew", **pad)
        _btn(out_f, "…",
             lambda: self._browse(self._v_outdir)).grid(
            row=0, column=2, **pad)
        out_f.columnconfigure(1, weight=1)

        # ── Cache ─────────────────────────────────────────────────────────────
        cache_f = ttk.LabelFrame(self, text=" Cache ")
        cache_f.pack(fill="x", padx=10, pady=4)

        cache_size = self._cache_size_mb()
        self._v_cache_lbl = tk.StringVar(
            value=f"Cache: {CACHE_DIR}  ({cache_size:.1f} MB)")
        ttk.Label(cache_f, textvariable=self._v_cache_lbl,
                  font=FONT_SMALL, foreground=CLR_FG2).grid(
            row=0, column=0, sticky="w", **pad)
        _btn(cache_f, "🗑 Clear Cache", self._clear_cache).grid(
            row=0, column=1, **pad)

        # ── Save button ───────────────────────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=8)
        _btn(self, "💾  Save Settings", self._save,
             style="Accent.TButton").pack(padx=10, pady=4, anchor="e")

    def _browse(self, var):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def _cache_size_mb(self) -> float:
        total = 0
        try:
            for f in os.listdir(CACHE_DIR):
                total += os.path.getsize(os.path.join(CACHE_DIR, f))
        except Exception:
            pass
        return total / (1024 * 1024)

    def _clear_cache(self):
        if messagebox.askyesno("Clear Cache",
                               "Delete all cached artwork and BGM files?"):
            import shutil
            try:
                shutil.rmtree(CACHE_DIR)
                os.makedirs(CACHE_DIR, exist_ok=True)
                self._v_cache_lbl.set(f"Cache: {CACHE_DIR}  (0.0 MB)")
                messagebox.showinfo("Done", "Cache cleared.")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _save(self):
        self._s["comp_level"]        = self._v_comp.get()
        self._s["apply_patches"]     = self._v_patches.get()
        self._s["fetch_artwork"]     = self._v_fetch_art.get()
        self._s["fetch_screenshot"]  = self._v_fetch_shot.get()
        self._s["animate_icon"]      = self._v_animate_icon.get()
        self._s["tgdb_api_key"]      = self._v_tgdb_key.get().strip()
        self._s["fetch_bgm"]         = self._v_fetch_bgm.get()
        self._s["loop_bgm"]          = self._v_loop_bgm.get()
        self._s["at3_bitrate"]       = self._v_at3_br.get()
        self._s["output_dir"]        = self._v_outdir.get()
        # BGM source order: enabled sources in display order
        order = ["khinsider", "archive", "youtube"]
        self._s["bgm_sources"] = [s for s in order
                                   if self._bgm_src_vars.get(s, tk.BooleanVar(value=True)).get()]
        # Apply TGDB key to environment immediately
        key = self._s["tgdb_api_key"]
        if key:
            os.environ["TGDB_API_KEY"] = key
        _save_settings(self._s)
        messagebox.showinfo("Saved", "Settings saved.")


# ── About tab ─────────────────────────────────────────────────────────────────

class AboutTab(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._build()

    def _build(self):
        # Title banner
        banner = tk.Frame(self, bg=CLR_ACCENT, height=56)
        banner.pack(fill="x")
        banner.pack_propagate(False)
        tk.Label(banner, text="PSX2PSP  Enhanced",
                 font=("Segoe UI", 18, "bold"),
                 bg=CLR_ACCENT, fg="#ffffff").pack(expand=True)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=10)

        # Version / description
        info_lines = [
            ("Version",     "1.0.0 – Python Edition"),
            ("Based on",    "PSX2PSP by KingSquitter + popstation.dll"),
            ("Python",      sys.version.split()[0]),
        ]
        for lbl, val in info_lines:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{lbl}:", width=12,
                      anchor="e", foreground=CLR_FG2).pack(side="left")
            ttk.Label(row, text=val, anchor="w").pack(side="left", padx=4)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)

        # Dependency status
        ttk.Label(body, text="Dependencies",
                  font=FONT_TITLE, foreground=CLR_HILIGHT).pack(anchor="w")

        deps = self._check_deps()
        dep_f = ttk.Frame(body)
        dep_f.pack(fill="x", pady=4)
        for i, (name, ok, note) in enumerate(deps):
            mark   = "✔" if ok else "✘"
            colour = CLR_GREEN if ok else CLR_RED
            ttk.Label(dep_f, text=f"{mark}  {name}",
                      foreground=colour, width=18).grid(
                row=i // 2, column=(i % 2) * 2,
                sticky="w", padx=8, pady=2)
            ttk.Label(dep_f, text=note,
                      foreground=CLR_FG2, font=FONT_SMALL).grid(
                row=i // 2, column=(i % 2) * 2 + 1,
                sticky="w", padx=2, pady=2)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)

        # Game DB info
        try:
            n = db_size()
            db_txt = f"gameInfo.db loaded – {n:,} game entries"
            db_col = CLR_GREEN
        except Exception:
            db_txt = "gameInfo.db not found"
            db_col = CLR_RED
        ttk.Label(body, text=db_txt, foreground=db_col,
                  font=FONT_SMALL).pack(anchor="w")

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)

        # Artwork/BGM sources
        ttk.Label(body, text="Artwork Sources",
                  font=FONT_TITLE, foreground=CLR_HILIGHT).pack(anchor="w")
        sources_txt = (
            "1. xlenore/psx-covers  (GitHub raw – serial-based, no key)\n"
            "2. Libretro Named_Boxarts  (game name, no key)\n"
            "3. TheGamesDB v2 API   (optional TGDB_API_KEY env var)\n"
            "4. Sony SCE TMDB API   (serial-based)\n"
            "5. DuckDuckGo images   (fallback web search)\n"
            "6. Auto-generated placeholder  (always available)\n"
            "\nScreenshot sources:\n"
            "  • Libretro Named_Snaps / Named_Titles\n"
            "  • TheGamesDB screenshots  (with API key)\n"
            "  • PSX Data Center  (HTML scrape)"
        )
        ttk.Label(body, text=sources_txt, justify="left",
                  foreground=CLR_FG2, font=FONT_SMALL).pack(anchor="w", pady=4)

        ttk.Label(body, text="BGM Sources",
                  font=FONT_TITLE, foreground=CLR_HILIGHT).pack(anchor="w")
        bgm_txt = (
            "1. KH Insider  (downloads.khinsider.com – no key, HTTP scrape)\n"
            "2. Internet Archive  (archive.org – free search API)\n"
            "3. YouTube via yt-dlp  (fallback, requires yt-dlp)\n"
            "\nAll sources → WAV via lame/ffmpeg → AT3 via at3tool.exe\n"
            "Custom audio files (MP3/WAV/OGG/FLAC) also accepted."
        )
        ttk.Label(body, text=bgm_txt, justify="left",
                  foreground=CLR_FG2, font=FONT_SMALL).pack(anchor="w", pady=4)

    def _check_deps(self):
        import shutil
        from .constants import AT3TOOL_EXE, LAME_EXE, POPSTATION_DLL, BASE_PBP

        deps = []
        # Python packages
        for pkg in ("PIL", "requests", "yt_dlp", "tqdm"):
            try:
                __import__(pkg)
                deps.append((pkg, True, "installed"))
            except ImportError:
                deps.append((pkg, False, "pip install " + pkg.replace("_", "-")))

        # External tools
        tools = [
            ("popstation.dll", POPSTATION_DLL),
            ("BASE.PBP",       BASE_PBP),
            ("at3tool.exe",    AT3TOOL_EXE),
            ("lame.exe",       LAME_EXE),
            ("ffmpeg",         shutil.which("ffmpeg") or ""),
        ]
        for name, path in tools:
            ok = bool(path and os.path.isfile(path))
            deps.append((name, ok, path if ok else "not found"))

        return deps


# ── Settings persistence helpers ──────────────────────────────────────────────

def _load_settings() -> dict:
    import configparser
    from .constants import SETTINGS_INI
    s = {
        "comp_level":       DEFAULT_COMPRESS,
        "apply_patches":    False,
        "fetch_artwork":    True,
        "fetch_screenshot": True,
        "animate_icon":     True,
        "tgdb_api_key":     "",
        "fetch_bgm":        True,
        "loop_bgm":         True,
        "at3_bitrate":      132,
        "output_dir":       OUTPUT_DIR,
        "bgm_sources":      ["khinsider", "archive", "youtube"],
    }
    cfg    = configparser.ConfigParser()
    py_ini = os.path.join(os.path.dirname(SETTINGS_INI), "psx2psp_py.ini")
    if os.path.isfile(py_ini):
        cfg.read(py_ini)
        sec = "Settings"
        if cfg.has_section(sec):
            for k in ("comp_level", "at3_bitrate"):
                if cfg.has_option(sec, k):
                    s[k] = cfg.getint(sec, k)
            for k in ("apply_patches", "fetch_artwork", "fetch_screenshot",
                       "animate_icon", "fetch_bgm", "loop_bgm"):
                if cfg.has_option(sec, k):
                    s[k] = cfg.getboolean(sec, k)
            for k in ("output_dir", "tgdb_api_key"):
                if cfg.has_option(sec, k):
                    s[k] = cfg.get(sec, k)
            if cfg.has_option(sec, "bgm_sources"):
                raw = cfg.get(sec, "bgm_sources")
                s["bgm_sources"] = [x.strip() for x in raw.split(",") if x.strip()]
    # Apply TGDB key to environment
    if s["tgdb_api_key"]:
        os.environ["TGDB_API_KEY"] = s["tgdb_api_key"]
    return s


def _save_settings(s: dict):
    import configparser
    from .constants import SETTINGS_INI
    cfg = configparser.ConfigParser()
    flat = {k: (", ".join(v) if isinstance(v, list) else str(v))
            for k, v in s.items()}
    cfg["Settings"] = flat
    py_ini = os.path.join(os.path.dirname(SETTINGS_INI), "psx2psp_py.ini")
    with open(py_ini, "w") as f:
        cfg.write(f)


# ── Game Search dialog ────────────────────────────────────────────────────────

class SearchDialog(tk.Toplevel):
    """
    Floating search window: type to search gameInfo.db,
    double-click or OK fills the fields in the calling tab.
    """

    def __init__(self, parent, on_select_cb):
        super().__init__(parent)
        self.title("Search Game Database")
        self.configure(bg=CLR_BG)
        self.resizable(True, True)
        self.geometry("540x380")
        self._cb = on_select_cb
        self._build()
        self.grab_set()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Search:").pack(side="left")
        self._v_q = tk.StringVar()
        self._v_q.trace_add("write", self._on_type)
        ttk.Entry(top, textvariable=self._v_q, width=36).pack(
            side="left", padx=6)
        _btn(top, "✖ Clear", lambda: self._v_q.set("")).pack(side="left")

        cols = ("game_id", "game_name", "region")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, hdr, w in zip(cols, ("Game ID", "Title", "Region"),
                               (110, 310, 80)):
            self._tree.heading(col, text=hdr)
            self._tree.column(col, width=w, minwidth=50)
        sb = ttk.Scrollbar(self, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(8, 0))
        sb.pack(side="right", fill="y", padx=(0, 8))
        self._tree.bind("<Double-1>", self._on_double)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=8, pady=6)
        _btn(btn_row, "OK",     self._on_ok).pack(side="right", padx=4)
        _btn(btn_row, "Cancel", self.destroy).pack(side="right")

        # Load first 50
        self._populate("")

    def _on_type(self, *_):
        self._populate(self._v_q.get())

    def _populate(self, q: str):
        self._tree.delete(*self._tree.get_children())
        results = search_games(q, max_results=50)
        for gi in results:
            self._tree.insert("", "end",
                              values=(gi.game_id, gi.game_name, gi.video_format))

    def _selected_gi(self):
        sel = self._tree.selection()
        if not sel:
            return None
        vals = self._tree.item(sel[0], "values")
        if vals:
            return lookup_game(vals[0])
        return None

    def _on_ok(self):
        gi = self._selected_gi()
        if gi:
            self._cb(gi)
        self.destroy()

    def _on_double(self, _event):
        gi = self._selected_gi()
        if gi:
            self._cb(gi)
            self.destroy()


# ── BGM Pick Dialog ──────────────────────────────────────────────────────────

class BgmPickDialog(tk.Toplevel):
    """
    Shows all BGM candidates from search_bgm() as a selectable list.
    For KHI candidates also shows the individual track list.
    Calls result_cb(chosen_candidate, track_index) on confirmation.
    """

    def __init__(self, parent, candidates: list, result_cb):
        super().__init__(parent)
        self.title("Select BGM Track")
        self.configure(bg=CLR_BG)
        self.geometry("680x460")
        self.resizable(True, True)
        self._candidates = candidates
        self._result_cb  = result_cb
        self._tracks: list = []
        self._build()
        self.grab_set()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=8, pady=6)

        # Left: album/result list
        left = ttk.LabelFrame(top, text=" Search Results ")
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))

        self._album_list = tk.Listbox(left, bg=CLR_PANEL, fg=CLR_FG,
            selectbackground=CLR_HILIGHT, font=FONT_SMALL,
            activestyle="none", height=18)
        sb1 = ttk.Scrollbar(left, command=self._album_list.yview)
        self._album_list.configure(yscrollcommand=sb1.set)
        self._album_list.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb1.pack(side="right", fill="y")
        self._album_list.bind("<<ListboxSelect>>", self._on_album_select)

        for c in self._candidates:
            self._album_list.insert("end", c.label())

        # Right: track list (only for KHI with multiple tracks)
        right = ttk.LabelFrame(top, text=" Tracks (KH Insider) ")
        right.pack(side="right", fill="both", expand=True, padx=(4, 0))

        self._track_list = tk.Listbox(right, bg=CLR_PANEL, fg=CLR_FG,
            selectbackground=CLR_HILIGHT, font=FONT_SMALL,
            activestyle="none", height=18)
        sb2 = ttk.Scrollbar(right, command=self._track_list.yview)
        self._track_list.configure(yscrollcommand=sb2.set)
        self._track_list.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb2.pack(side="right", fill="y")

        self._status = ttk.Label(self, text="Select an album, then a track (or leave blank for auto-pick).",
                                  foreground=CLR_FG2, font=FONT_SMALL)
        self._status.pack(fill="x", padx=10)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=6)
        _btn(btns, "✔  Use Selected", self._confirm,
             style="Accent.TButton").pack(side="right", padx=4)
        _btn(btns, "✖  Cancel", self.destroy).pack(side="right")
        _btn(btns, "🔄  Load Tracks", self._load_tracks).pack(side="left", padx=4)

    def _on_album_select(self, _event=None):
        sel = self._album_list.curselection()
        if not sel:
            return
        c = self._candidates[sel[0]]
        self._status.configure(text=f"Selected: {c.label()}")
        self._track_list.delete(0, "end")
        self._track_list.insert("end", "(click 'Load Tracks' to populate)")

    def _load_tracks(self):
        sel = self._album_list.curselection()
        if not sel:
            return
        c = self._candidates[sel[0]]
        if c.source != "khinsider":
            self._status.configure(text="Track list only available for KH Insider results.")
            return
        self._status.configure(text="Loading tracks…")
        self._track_list.delete(0, "end")

        def _work():
            from .bgm import get_khi_tracks
            tracks = get_khi_tracks(c)
            self.after(0, lambda: self._populate_tracks(tracks))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_tracks(self, tracks: list):
        self._tracks = tracks
        self._track_list.delete(0, "end")
        self._track_list.insert("end", "(auto-pick best track)")
        for i, (name, _) in enumerate(tracks, 1):
            import urllib.parse
            disp = urllib.parse.unquote(name).replace("%20", " ")
            self._track_list.insert("end", f"{i:02d}. {disp}")
        self._status.configure(text=f"{len(tracks)} tracks loaded.")

    def _confirm(self):
        sel = self._album_list.curselection()
        if not sel:
            self.destroy(); return
        album_idx  = sel[0]
        track_sel  = self._track_list.curselection()
        track_idx  = track_sel[0] if track_sel else 0  # 0 = auto
        self._result_cb(self._candidates[album_idx], track_idx)
        self.destroy()


# ── Image Pick Dialog ─────────────────────────────────────────────────────────

class ImagePickDialog(tk.Toplevel):
    """
    Shows all image candidates as thumbnail grid.
    result_cb(chosen_candidate) called on confirmation.
    """

    _THUMB = (120, 90)

    def __init__(self, parent, candidates: list, title: str, result_cb):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=CLR_BG)
        self.geometry("740x520")
        self.resizable(True, True)
        self._candidates  = candidates
        self._result_cb   = result_cb
        self._thumb_refs:  list = []
        self._selected_idx = tk.IntVar(value=-1)
        self._build()
        self.grab_set()
        # Start loading thumbnails in background
        threading.Thread(target=self._load_thumbs, daemon=True).start()

    def _build(self):
        ttk.Label(self, text="Click a thumbnail to select, then press Use Selected.",
                  foreground=CLR_FG2, font=FONT_SMALL).pack(anchor="w", padx=8, pady=(6, 2))

        # Scrollable canvas grid
        frame    = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8)
        self._canvas = tk.Canvas(frame, bg=CLR_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._grid_frame = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._grid_frame, anchor="nw")
        self._grid_frame.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))

        # Placeholder cells
        self._cells: list = []
        cols = 5
        for i, c in enumerate(self._candidates):
            cell = ttk.Frame(self._grid_frame, relief="flat")
            cell.grid(row=i // cols, column=i % cols, padx=4, pady=4)
            cv = tk.Canvas(cell, width=self._THUMB[0], height=self._THUMB[1],
                           bg=CLR_PANEL, highlightthickness=2,
                           highlightbackground=CLR_PANEL)
            cv.pack()
            cv.create_text(self._THUMB[0]//2, self._THUMB[1]//2,
                           text="Loading…", fill=CLR_FG2, font=FONT_SMALL)
            lbl = ttk.Label(cell, text=c.source, font=FONT_SMALL,
                            foreground=CLR_FG2, wraplength=self._THUMB[0])
            lbl.pack()
            idx = i
            cv.bind("<Button-1>", lambda e, ii=idx: self._select(ii))
            lbl.bind("<Button-1>", lambda e, ii=idx: self._select(ii))
            self._cells.append((cv, lbl))

        self._status_lbl = ttk.Label(self, text="", foreground=CLR_FG2, font=FONT_SMALL)
        self._status_lbl.pack(fill="x", padx=10)
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=6)
        _btn(btns, "✔  Use Selected", self._confirm,
             style="Accent.TButton").pack(side="right", padx=4)
        _btn(btns, "✖  Cancel", self.destroy).pack(side="right")

    def _load_thumbs(self):
        from .artwork import resolve_candidate
        for i, c in enumerate(self._candidates):
            img = resolve_candidate(c)
            if img:
                tw, th = self._THUMB
                scale = min(tw / img.width, th / img.height)
                nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
                thumb = img.resize((nw, nh), Image.LANCZOS)
                tk_img = ImageTk.PhotoImage(thumb)
                self.after(0, lambda ii=i, tk=tk_img, nw=nw, nh=nh:
                           self._set_thumb(ii, tk, nw, nh))

    def _set_thumb(self, idx: int, tk_img, nw: int, nh: int):
        cv, lbl = self._cells[idx]
        self._thumb_refs.append(tk_img)
        cv.delete("all")
        ox = (self._THUMB[0] - nw) // 2
        oy = (self._THUMB[1] - nh) // 2
        cv.create_image(ox, oy, anchor="nw", image=tk_img)

    def _select(self, idx: int):
        self._selected_idx.set(idx)
        for i, (cv, _) in enumerate(self._cells):
            cv.configure(highlightbackground=CLR_HILIGHT if i == idx else CLR_PANEL,
                         highlightthickness=3 if i == idx else 2)
        c = self._candidates[idx]
        self._status_lbl.configure(text=c.display_label())

    def _confirm(self):
        idx = self._selected_idx.get()
        if idx < 0:
            self.destroy(); return
        self._result_cb(self._candidates[idx])
        self.destroy()


# ── Main Application window ───────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PSX2PSP Enhanced – Python Edition")
        self.configure(bg=CLR_BG)
        self.geometry("900x680")
        self.minsize(760, 540)
        _apply_theme(self)

        # Ensure output/cache dirs exist
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR,  exist_ok=True)

        self._settings = _load_settings()
        self._build_menu()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        menu = tk.Menu(self, bg=CLR_PANEL, fg=CLR_FG,
                       activebackground=CLR_HILIGHT, activeforeground="#fff",
                       relief="flat")
        self.configure(menu=menu)

        file_m = tk.Menu(menu, tearoff=0, bg=CLR_PANEL, fg=CLR_FG,
                          activebackground=CLR_HILIGHT)
        menu.add_cascade(label="File", menu=file_m)
        file_m.add_command(label="Open Disc Image…",
                           command=self._menu_open)
        file_m.add_command(label="Open Output Folder",
                           command=self._open_output)
        file_m.add_separator()
        file_m.add_command(label="Exit", command=self._on_close)

        tools_m = tk.Menu(menu, tearoff=0, bg=CLR_PANEL, fg=CLR_FG,
                           activebackground=CLR_HILIGHT)
        menu.add_cascade(label="Tools", menu=tools_m)
        tools_m.add_command(label="Search Game Database…",
                             command=self._show_search)
        tools_m.add_command(label="Clear Artwork Cache",
                             command=self._clear_cache)
        tools_m.add_separator()
        tools_m.add_command(label="Open Cache Folder",
                             command=lambda: _open_folder(CACHE_DIR))

        help_m = tk.Menu(menu, tearoff=0, bg=CLR_PANEL, fg=CLR_FG,
                          activebackground=CLR_HILIGHT)
        menu.add_cascade(label="Help", menu=help_m)
        help_m.add_command(label="About", command=self._show_about)

    # ── Main UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header bar
        hdr = tk.Frame(self, bg=CLR_ACCENT, height=38)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⚡ PSX2PSP Enhanced",
                 font=("Segoe UI", 13, "bold"),
                 bg=CLR_ACCENT, fg="#ffffff").pack(side="left", padx=12)
        # DB status in header
        try:
            n = db_size()
            db_lbl = f"DB: {n:,} games"
            db_col = "#80ffc0"
        except Exception:
            db_lbl = "DB: not loaded"
            db_col = CLR_RED
        tk.Label(hdr, text=db_lbl, font=FONT_SMALL,
                 bg=CLR_ACCENT, fg=db_col).pack(side="right", padx=12)

        # Notebook tabs
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)

        self._single_tab = SingleGameTab(nb, self._settings)
        self._batch_tab  = BatchTab(nb, self._settings)
        self._set_tab    = SettingsTab(nb, self._settings)
        self._about_tab  = AboutTab(nb)

        nb.add(self._single_tab, text="  Single Game  ")
        nb.add(self._batch_tab,  text="  Batch  ")
        nb.add(self._set_tab,    text="  Settings  ")
        nb.add(self._about_tab,  text="  About  ")

        self._nb = nb

        # Status bar
        self._status_var = tk.StringVar(value="Ready.")
        status = tk.Label(self, textvariable=self._status_var,
                          bg="#0d0d20", fg=CLR_FG2,
                          font=FONT_SMALL, anchor="w", padx=8)
        status.pack(fill="x", side="bottom")

    # ── Menu handlers ─────────────────────────────────────────────────────────

    def _menu_open(self):
        """Open a disc and jump to Single Game tab."""
        path = filedialog.askopenfilename(
            title="Select Disc Image",
            filetypes=[("Disc Images", "*.bin *.iso *.img *.cue"),
                       ("All Files", "*.*")])
        if path:
            tab = self._single_tab
            tab._disc_paths.clear()
            tab._disc_list.delete(0, "end")
            tab._disc_paths.append(path)
            tab._disc_list.insert("end", os.path.basename(path))
            tab._auto_detect()
            self._nb.select(0)

    def _open_output(self):
        out = self._settings.get("output_dir", OUTPUT_DIR) or OUTPUT_DIR
        os.makedirs(out, exist_ok=True)
        _open_folder(out)

    def _show_search(self):
        def _fill(gi):
            tab = self._single_tab
            tab._v_title.set(gi.game_name)
            tab._v_stitle.set(gi.save_desc or gi.game_name)
            tab._v_gid.set(gi.game_id_formatted)
            tab._v_sid.set(gi.save_folder)
            tab._v_serial.set(gi.serial_norm)
            tab._v_region.set(gi.video_format)
            tab._preview.set_info(gi.game_name, gi.serial_norm,
                                   gi.video_format)
            self._nb.select(0)
        SearchDialog(self, _fill)

    def _show_about(self):
        self._nb.select(3)

    def _clear_cache(self):
        if messagebox.askyesno("Clear Cache",
                               "Delete all cached artwork and BGM?"):
            import shutil
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            os.makedirs(CACHE_DIR, exist_ok=True)
            self._status_var.set("Cache cleared.")

    def _menu_open_path(self, path: str):
        """Pre-load a disc image (called from launcher with file argument)."""
        if path and os.path.isfile(path):
            tab = self._single_tab
            tab._disc_paths.clear()
            tab._disc_list.delete(0, "end")
            tab._disc_paths.append(path)
            tab._disc_list.insert("end", os.path.basename(path))
            tab._auto_detect()
            self._nb.select(0)

    def _on_close(self):
        _save_settings(self._settings)
        self.destroy()
