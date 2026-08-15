"""
KeyPrism - Roblox Piano Auto Player
A single, polished auto player for Roblox piano.

Features:
- Load .mid files (picker, Ctrl+O, double-click library, auto-load demo)
- 61 / 88 key support with octave wrapping
- Synthesia-style falling notes synced to playback
- Speed, Transpose (manual + AUTO), Countdown, Loop controls
- Track selector + local MIDI library
- Visual piano (highlights played keys)
- Global hotkeys (F6 Play/Pause, F7 Stop)
- Focus delay to switch to Roblox

UI: synthwave dark — deep indigo surfaces, violet + electric cyan brand
(KeyPrism crystal mark). Design system at design-system-nanoplayer/MASTER.md.
"""

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
import mido

# Try to use customtkinter for the modern look, fallback to tkinter
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    USE_CTK = True
except ImportError:
    USE_CTK = False
    from tkinter import ttk

from engine import MidiParser, RobloxPianoPlayer
from mapping import KEYS_61, KEYS_88_FULL
from cloud import CloudClient, CloudError

APP_NAME = "KeyPrism - Roblox Piano Auto Player"
VERSION = "2.0.0"

# ── Design tokens ────────────────────────────────────────────────
# Synthwave dark: deep indigo surfaces, neon violet primary, rose CTA.
# Text colors are chosen for >=4.5:1 contrast on their surfaces.
COLORS = {
    "bg": "#0F0F23",
    "surface": "#171730",
    "surface_hover": "#232345",
    "card": "#1C1C38",
    "border": "#2C2C52",
    "accent": "#7C3AED",
    "accent_hover": "#8B5CF6",
    "accent_soft": "#A78BFA",
    # Brand duotone follows the logo: violet (left) + electric cyan (right)
    "action": "#22D3EE",
    "action_hover": "#67E8F9",
    "action_text": "#0F172A",
    "text": "#E6E6F5",
    "text_dim": "#9DA3C9",
    "text_faint": "#6E7399",
    "success": "#34D399",
    "warn": "#FBBF24",
    "danger": "#F87171",
    "key_white": "#F4F4FB",
    "key_black": "#252546",
    "key_active": "#7C3AED",
    "key_active_light": "#C4B5FD",
}

# Status pill / dot states -> (dot color, short word)
STATUS_STATES = {
    "idle":      {"color": "#565A80", "word": "IDLE"},
    "loading":   {"color": "#565A80", "word": "LOADING"},
    "loaded":    {"color": "#A78BFA", "word": "READY"},
    "countdown": {"color": "#FBBF24", "word": "COUNTDOWN"},
    "playing":   {"color": "#22D3EE", "word": "PLAYING"},
    "paused":    {"color": "#FBBF24", "word": "PAUSED"},
    "finished":  {"color": "#34D399", "word": "DONE"},
    "stopped":   {"color": "#565A80", "word": "STOPPED"},
    "error":     {"color": "#F87171", "word": "ERROR"},
}

# Cloud (Railway) connection states
CLOUD_STATES = {
    "offline":    {"color": "#565A80", "word": "CLOUD — OFFLINE"},
    "connecting": {"color": "#FBBF24", "word": "CLOUD — CONNECTING"},
    "online":     {"color": "#34D399", "word": "CLOUD — ONLINE"},
    "syncing":    {"color": "#22D3EE", "word": "CLOUD — SYNCING"},
    "error":      {"color": "#F87171", "word": "CLOUD — ERROR"},
}

# Gentle pulse pairs for the status dot in active states (motion-22)
PULSE_PAIRS = {
    "countdown": ("#FBBF24", "#FDE68A"),
    "playing":   ("#22D3EE", "#A5F3FC"),
}


def _resource(name):
    """Resolve a bundled asset (script dir, or _MEIPASS when frozen)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _app_dir():
    """Directory for user data (history/cache) — beside the app or the exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


HISTORY_FILE = _app_dir() / "keyprism_history.json"
RECENT_MAX = 6

# ── Cloud: hardcoded Railway URL (auto-connects on startup, no typing needed) ──
DEFAULT_CLOUD_URL = "https://lavish-generosity-production-1ace.up.railway.app"


def _reduced_motion():
    """Respect the OS-level reduced-motion preference (Windows: Show animations)."""
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_bool()
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return not enabled.value
    except Exception:
        return False


def fmt_time(seconds):
    """0:00 style timestamp."""
    if seconds is None or seconds < 0:
        seconds = 0
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


class VisualPiano:
    """Canvas visual piano for tkinter/customtkinter.

    - 61-key: real piano black-key pattern (C# D# F# G# A# per octave)
    - 88-key: community layout (lowercase row = white, shifted row = black)
    - Resize-aware: re-reads canvas size, debounced via <Configure>
    - Key flashes are scheduled with after(), not threads
    """

    def __init__(self, canvas, mode="61", y_offset=0):
        self.canvas = canvas
        self.mode = mode
        self.y_offset = y_offset   # falling-notes area height above the keys
        self.key_rects = {}   # char -> (rect_id, is_black)
        self.key_x = {}       # char -> center x (for falling notes)
        self._flash_jobs = {}  # rect_id -> after-id

    def _key_is_black(self, index, char):
        if self.mode == "61":
            # Standard piano: black keys at semitone offsets 1,3,6,8,10
            return (index % 12) in {1, 3, 6, 8, 10}
        # 88-key community mapping groups lower row (white) then shifted row (black)
        return not (char.islower() or char.isdigit())

    def draw(self, width=None, height=None):
        canvas = self.canvas
        if width is None:
            width = canvas.winfo_width()
        if height is None:
            height = canvas.winfo_height()
        if width < 80 or height < 30:
            return  # not mapped yet

        canvas.delete("all")
        self.key_rects.clear()
        self.key_x.clear()
        self._flash_jobs.clear()

        keys = KEYS_61 if self.mode == "61" else KEYS_88_FULL
        n = len(keys)
        key_w = width / n
        label_font = ("Consolas", 6) if n > 70 else ("Consolas", 7)

        # Synthwave grid backdrop above the keys (falling-notes area)
        if self.y_offset > 0:
            grid_bg = "#12122A"
            canvas.create_rectangle(0, 0, width, self.y_offset, fill=grid_bg, outline="")
            for i in range(1, 8):
                x = width / 8 * i
                canvas.create_line(x, 4, x, self.y_offset, fill=COLORS["border"], dash=(2, 4))
            for i in range(1, 4):
                y = self.y_offset * i / 4
                canvas.create_line(0, y, width, y, fill="#20203F")
            canvas.create_line(0, self.y_offset, width, self.y_offset, fill=COLORS["border"])

        for i, char in enumerate(keys):
            x0 = i * key_w
            x1 = (i + 1) * key_w
            y0, y1 = self.y_offset + 4.0, float(height - 4)
            black = self._key_is_black(i, char)
            fill = COLORS["key_black"] if black else COLORS["key_white"]
            outline = COLORS["border"] if black else COLORS["card"]
            rect = canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=1)
            label_color = COLORS["accent_soft"] if black else "#7A7FA8"
            canvas.create_text((x0 + x1) / 2, y1 - 9, text=char, font=label_font, fill=label_color)
            self.key_rects[char] = (rect, black)
            self.key_x[char] = (x0 + x1) / 2

    def highlight(self, char, duration=0.18):
        if char not in self.key_rects:
            return
        rect_id, is_black = self.key_rects[char]
        canvas = self.canvas

        # Extend an existing flash instead of fighting it
        job = self._flash_jobs.pop(rect_id, None)
        if job is not None:
            try:
                canvas.after_cancel(job)
            except Exception:
                pass

        flash_fill = COLORS["key_active"] if is_black else COLORS["key_active_light"]
        original = COLORS["key_black"] if is_black else COLORS["key_white"]
        try:
            canvas.itemconfig(rect_id, fill=flash_fill)
        except Exception:
            return

        def unflash():
            self._flash_jobs.pop(rect_id, None)
            try:
                canvas.itemconfig(rect_id, fill=original)
            except Exception:
                pass

        self._flash_jobs[rect_id] = canvas.after(int(duration * 1000), unflash)


class NanoApp:
    def __init__(self):
        if USE_CTK:
            self.root = ctk.CTk()
            self.root.geometry("1040x720")
            self.root.minsize(880, 620)
            self.root.title(f"{APP_NAME} v{VERSION}")
            self.root.configure(fg_color=COLORS["bg"])
        else:
            self.root = tk.Tk()
            self.root.geometry("980x660")
            self.root.minsize(860, 600)
            self.root.title(f"{APP_NAME} v{VERSION}")
            self.root.configure(bg=COLORS["bg"])

        # Fonts — pick the first family available on this system
        self.font_body = self._pick_font(["Segoe UI", "Arial", "Helvetica"])
        self.font_brand = self._pick_font(
            ["Bahnschrift SemiCondensed", "Bahnschrift", "Segoe UI Semibold", self.font_body]
        )
        self.font_mono = self._pick_font(["Consolas", "Cascadia Mono", "Courier New", "Courier"])

        self.parser = None
        self.events = []
        self.player = RobloxPianoPlayer(on_note_callback=self._on_note_played)
        self.current_file = None
        self.transpose = 0
        self.speed = 1.0
        self.mode = "61"
        self.focus_delay = 3
        self.wrap_octave = True
        self.enabled_tracks = None
        self.chord_threshold = 15
        self.total_time = 0.0

        # Thread-safe bridge: the player thread pushes UI messages here,
        # the UI thread drains them in _poll_ui_queue (never touch tk from
        # a worker thread).
        self.ui_queue = queue.Queue()

        # Polish / micro-interaction state
        self._reduced = _reduced_motion()
        self.status_state = "idle"
        self._dot_pulse_job = None
        self._count_flash_job = None
        self._count_visible = False
        # Sink stale queued progress messages after stop() (see _apply_progress)
        self._sink_progress = False
        # Falling-notes clock (driven by progress callbacks)
        self._last_t = None
        self._last_cb = 0.0
        self.fall_h = 190

        # Hub sidebar state: recent files, stats, and a cached library index
        self._lib_cache = {}
        self._scan_thread = None
        self._lib_render_job = None
        self._search_job = None
        self.search_var = tk.StringVar(value="")
        self._load_history()

        # Cloud (Railway) connection state
        self.cloud_online = False
        self.cloud_client = None
        self._cloud_thread = None
        self.cloud_url_var = tk.StringVar(
            value=self.history.get("cloud_url") or DEFAULT_CLOUD_URL)

        self._set_window_icon()
        self._build_ui()
        self._bind_hotkeys()
        self._poll_ui_queue()
        self.root.after(50, self._anim_loop)

        # Create midi folder + load first available song
        Path("midi_files").mkdir(exist_ok=True)
        midis = sorted(Path("midi_files").glob("*.mid"))
        if midis:
            self.root.after(200, lambda p=str(midis[0]): self.load_midi_path(p))

        # Auto-connect to cloud INSTANTLY if URL is set
        if self.cloud_url_var.get().strip():
            self.root.after(300, self._cloud_auto_connect)

        # URL validation on focus-out
        if USE_CTK:
            self.cloud_url_entry.bind("<FocusOut>", self._on_cloud_url_validate)

    # ── helpers ──────────────────────────────────────────────────

    def _pick_font(self, candidates):
        try:
            import tkinter.font as tkfont
            available = set(tkfont.families(self.root))
            for fam in candidates:
                if fam in available:
                    return fam
        except Exception:
            pass
        return candidates[0]

    def _make_dot(self, parent, bg, color):
        """Small status dot on a canvas. Returns (canvas, item_id)."""
        c = tk.Canvas(parent, width=10, height=10, bg=bg, highlightthickness=0)
        cid = c.create_oval(1, 1, 9, 9, fill=color, outline="")
        return c, cid

    def _set_dot(self, canvas, item, color):
        try:
            canvas.itemconfig(item, fill=color)
        except Exception:
            pass

    def _set_status(self, text, state="idle"):
        self.status_state = state
        st = STATUS_STATES.get(state, STATUS_STATES["idle"])
        if hasattr(self, "label_status"):
            try:
                self.label_status.configure(text=text)
            except Exception:
                pass
        if hasattr(self, "status_label"):
            try:
                self.status_label.configure(text=st["word"])
            except Exception:
                pass
        if hasattr(self, "status_dot") and hasattr(self, "status_dot_id"):
            self._set_dot(self.status_dot, self.status_dot_id, st["color"])
        if hasattr(self, "transport_dot") and hasattr(self, "transport_dot_id"):
            self._set_dot(self.transport_dot, self.transport_dot_id, st["color"])
        # One ambient indicator for active states (motion-22): pulse while
        # counting down or playing, keep steady otherwise.
        if state in ("countdown", "playing") and not self._reduced:
            self._start_dot_pulse()
        else:
            self._stop_dot_pulse()

    def _push(self, *msg):
        """Queue a UI update from a worker thread (never call tk there)."""
        try:
            self.ui_queue.put_nowait(msg)
        except Exception:
            pass

    def _poll_ui_queue(self):
        """Drain UI messages on the main thread; reschedules itself."""
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._set_status(msg[1], msg[2])
                elif kind == "countdown":
                    self._set_status(msg[1], "countdown")
                    if USE_CTK and hasattr(self, "label_count"):
                        self._set_countdown_visible(True)
                        self.label_count.configure(text=str(msg[2]))
                        if not self._reduced:
                            self._flash_count_tick()
                elif kind == "note":
                    self.visual_piano.highlight(msg[1])
                elif kind == "progress":
                    self._apply_progress(msg[1], msg[2], msg[3], msg[4], msg[5])
                elif kind == "played":
                    st = self.history.setdefault("stats", {})
                    st["notes_played"] = st.get("notes_played", 0) + msg[1]
                    st["songs_played"] = st.get("songs_played", 0) + 1
                    self._save_history()
                    self._render_hub_stats()
                    self._cloud_push_stats()
                elif kind == "lib_item":
                    self._lib_cache[msg[1]] = msg[2]
                    self._save_history()
                    if self._lib_render_job is not None:
                        try:
                            self.root.after_cancel(self._lib_render_job)
                        except Exception:
                            pass
                    self._lib_render_job = self.root.after(250, self._render_lib)
                elif kind == "lib_done":
                    self._save_history()
                    self._render_hub_stats()
                    self._render_lib()
                elif kind == "cloud_connected":
                    self.cloud_online = True
                    self.cloud_client = CloudClient(self.cloud_url_var.get().strip())
                    self._cloud_state("online",
                                      f"Connected — stats merged (songs {msg[2]}, notes {msg[3]})")
                    self.btn_cloud_connect.configure(text="DISCONNECT")
                    self.btn_cloud_sync.configure(state="normal")
                    self.btn_cloud_backup.configure(state="normal")
                    self.history["cloud_last_sync"] = time.time()
                    self._save_history()
                    self._render_hub_stats()
                    # Auto-sync library on first connect (fire once)
                    if not getattr(self, "_cloud_initial_synced", False):
                        self._cloud_initial_synced = True
                        self.root.after(500, self._on_cloud_backup)
                    # Start keepalive ping thread
                    if not getattr(self, "_keepalive_started", False):
                        self._keepalive_started = True
                        threading.Thread(target=self._cloud_keepalive, daemon=True).start()
                elif kind == "cloud_synced":
                    st = self.history.setdefault("stats", {})
                    st["songs_played"] = max(st.get("songs_played", 0), msg[1].get("songs_played", 0))
                    st["notes_played"] = max(st.get("notes_played", 0), msg[1].get("notes_played", 0))
                    self._save_history()
                    self._render_hub_stats()
                    self.history["cloud_last_sync"] = time.time()
                    self._save_history()
                    self._cloud_state("online", "Synced " +
                                      time.strftime("%H:%M", time.localtime(self.history["cloud_last_sync"])))
                    self.btn_cloud_sync.configure(state="normal")
                    self.btn_cloud_backup.configure(state="normal")
                elif kind == "cloud_library_done":
                    self.history["cloud_last_sync"] = time.time()
                    self._save_history()
                    self._cloud_state("online",
                                      f"Library sync: {msg[1]} uploaded, {msg[2]} downloaded")
                    self.btn_cloud_sync.configure(state="normal")
                    self.btn_cloud_backup.configure(state="normal")
                    self._scan_library()
                elif kind == "cloud_url_valid":
                    self._cloud_state("offline", f"URL is live — hit CONNECT to link ({msg[1]})")
                elif kind == "cloud_error":
                    self.cloud_online = False
                    self.cloud_client = None
                    self._cloud_state("error", msg[1])
                    self.btn_cloud_connect.configure(text="CONNECT", state="normal")
                    self.btn_cloud_sync.configure(state="disabled")
                    self.btn_cloud_backup.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(30, self._poll_ui_queue)

    def _apply_progress(self, pct, elapsed, t, status_text, state):
        self._set_countdown_visible(False)
        # A pause/stop may have raced ahead of queued player-thread messages;
        # drop stale ones so "Paused"/"Stopped" isn't overwritten (interactivity-21).
        if self._sink_progress and state in ("playing", "finished"):
            return
        if state == "playing" and not self.player._pause_flag.is_set():
            return
        if t is not None and t >= 0:
            self._last_t = t
            self._last_cb = time.time()
        if USE_CTK:
            self.progress.set(pct)
            self.label_elapsed.configure(text=elapsed)
        else:
            try:
                self.progress["value"] = int(pct * 1000)
            except Exception:
                pass
        self._set_status(status_text, state=state)

    # ── micro-interactions (interface-details) ──────────────────

    def _start_dot_pulse(self):
        if self._dot_pulse_job is not None or not hasattr(self, "status_dot"):
            return
        light = [False]

        def tick():
            pulsing = self.status_state in ("countdown", "playing")
            if pulsing and self.status_state == "playing" and not self.player._pause_flag.is_set():
                pulsing = False
            if not pulsing:
                self._dot_pulse_job = None
                st = STATUS_STATES.get(self.status_state, STATUS_STATES["idle"])
                self._set_dot(self.status_dot, self.status_dot_id, st["color"])
                if hasattr(self, "transport_dot"):
                    self._set_dot(self.transport_dot, self.transport_dot_id, st["color"])
                return
            st = STATUS_STATES.get(self.status_state, STATUS_STATES["idle"])
            pair = PULSE_PAIRS.get(self.status_state, (st["color"], st["color"]))
            color = pair[1] if light[0] else pair[0]
            self._set_dot(self.status_dot, self.status_dot_id, color)
            if hasattr(self, "transport_dot"):
                self._set_dot(self.transport_dot, self.transport_dot_id, color)
            light[0] = not light[0]
            self._dot_pulse_job = self.root.after(500, tick)

        self._dot_pulse_job = self.root.after(500, tick)

    def _stop_dot_pulse(self):
        if self._dot_pulse_job is not None:
            try:
                self.root.after_cancel(self._dot_pulse_job)
            except Exception:
                pass
            self._dot_pulse_job = None
        if hasattr(self, "status_dot"):
            st = STATUS_STATES.get(self.status_state, STATUS_STATES["idle"])
            self._set_dot(self.status_dot, self.status_dot_id, st["color"])
            if hasattr(self, "transport_dot"):
                self._set_dot(self.transport_dot, self.transport_dot_id, st["color"])

    def _set_countdown_visible(self, visible):
        """Swap the progress bar slot for a big countdown numeral (no layout shift)."""
        if not USE_CTK or not hasattr(self, "label_count"):
            return
        if visible and not self._count_visible:
            self.progress.pack_forget()
            self.label_count.pack(side="left", fill="x", expand=True)
            self._count_visible = True
        elif not visible and self._count_visible:
            self.label_count.pack_forget()
            self.progress.pack(side="left", fill="x", expand=True, pady=14)
            self._count_visible = False

    def _flash_count_tick(self):
        """Brief white flash on each countdown tick (motion-22 ambient feedback)."""
        if self._count_flash_job is not None:
            try:
                self.root.after_cancel(self._count_flash_job)
            except Exception:
                pass
        try:
            self.label_count.configure(text_color="#FFFFFF")
        except Exception:
            return

        def settle():
            self._count_flash_job = None
            try:
                self.label_count.configure(text_color=COLORS["warn"])
            except Exception:
                pass

        self._count_flash_job = self.root.after(180, settle)

    def _flash_button(self, btn):
        """Acknowledge a dead end with a brief danger flash (motion-16/24)."""
        if self._reduced:
            return
        try:
            original = btn.cget("fg_color")
            btn.configure(fg_color=COLORS["danger"])

            def restore():
                try:
                    btn.configure(fg_color=original)
                except Exception:
                    pass

            self.root.after(220, restore)
        except Exception:
            pass

    def _bind_focus_ring(self, widget):
        """Visible accent focus ring for keyboard users (accessibility-1).
        Note: CTk widgets reject "transparent" as a border color, so the
        unfocused state uses a real color at border_width=0 (invisible)."""
        try:
            widget.bind("<FocusIn>", lambda e: widget.configure(
                border_width=2, border_color=COLORS["accent_soft"]))
            widget.bind("<FocusOut>", lambda e: widget.configure(
                border_width=0, border_color=COLORS["card"]))
        except Exception:
            pass

    @staticmethod
    def _plural(n, word):
        n = int(n)
        return f"{n:,} {word}{'' if n == 1 else 's'}"

    @staticmethod
    def _truncate_path(path, max_parts=3):
        """Keep the root and the file name; collapse the middle (typography-3)."""
        p = str(path).replace("/", "\\")
        parts = [x for x in p.split("\\") if x]
        if len(parts) <= max_parts:
            return p
        if len(parts[0]) == 2 and parts[0][1] == ":":
            return parts[0] + "\\…\\" + parts[-1]
        return parts[0] + "\\…\\" + parts[-1]

    def _stat_cell(self, parent, row, col, caption):
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=row, column=col, sticky="nsew", padx=6, pady=5)
        value = ctk.CTkLabel(cell, text="—", font=(self.font_mono, 14, "bold"),
                             text_color=COLORS["text"], anchor="w")
        value.pack(anchor="w")
        ctk.CTkLabel(cell, text=caption, font=(self.font_body, 8, "bold"),
                     text_color=COLORS["text_faint"], anchor="w").pack(anchor="w")
        return value

    def _keycap(self, parent, label):
        cap = ctk.CTkFrame(parent, fg_color=COLORS["surface"], corner_radius=5,
                           border_width=1, border_color=COLORS["border"], height=22)
        cap.pack(side="left", padx=(0, 6))
        cap.pack_propagate(False)
        ctk.CTkLabel(cap, text=label, font=(self.font_mono, 9, "bold"),
                     text_color=COLORS["accent_soft"]).pack(padx=7, pady=1)
        return cap

    def _set_window_icon(self):
        """Brand the window + taskbar with the crystal logo.

        tkinter's iconphoto/iconbitmap silently fail to register an icon on
        some Tk 8.6 builds (verified: WM_GETICON returns 0 on the toplevel),
        so also force it at the Win32 level via WM_SETICON.
        """
        self._icon_img = None
        self._win_hicon = None
        try:
            self.root.iconbitmap(str(_resource("app.ico")))
        except Exception:
            pass
        try:
            self._icon_img = tk.PhotoImage(file=str(_resource("logo_header.png")))
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass
        if os.name != "nt":
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            hicon = u32.LoadImageW(
                None, str(_resource("app.ico")), 1, 0, 0, 0x10 | 0x40)
            if hicon:
                hwnd = int(self.root.winfo_id())
                parent = u32.GetParent(hwnd) or hwnd  # real toplevel
                u32.SendMessageW(parent, 0x0080, 1, hicon)  # WM_SETICON BIG
                u32.SendMessageW(parent, 0x0080, 0, hicon)  # WM_SETICON SMALL
                self._win_hicon = hicon
        except Exception:
            pass

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self):
        if USE_CTK:
            self._build_ctk_ui()
        else:
            self._build_tk_ui()

    def _section_card(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12)
        card.pack(fill="x", pady=(0, 10))
        return card

    def _section_title(self, card, text):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10, 5))
        bar = tk.Canvas(row, width=3, height=12, bg=COLORS["card"], highlightthickness=0)
        bar.create_rectangle(0, 0, 3, 12, fill=COLORS["accent"], outline="")
        bar.pack(side="left", padx=(0, 7))
        ctk.CTkLabel(row, text=text, font=(self.font_body, 10, "bold"),
                     text_color=COLORS["text_dim"]).pack(side="left")

    def _build_ctk_ui(self):
        root = self.root

        # ── Header ──────────────────────────────────────────────
        header = ctk.CTkFrame(root, fg_color=COLORS["surface"], corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        # Brand logo — the synthwave crystal mark (transparent PNG)
        self.logo_img = tk.PhotoImage(file=str(_resource("logo_header.png")))
        tk.Label(header, image=self.logo_img, bg=COLORS["surface"]).pack(
            side="left", padx=(18, 10), pady=12)

        ctk.CTkLabel(header, text="KEYPRISM", font=(self.font_brand, 19, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        ctk.CTkLabel(header, text="ROBLOX PIANO AUTO", font=(self.font_body, 10, "bold"),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(12, 0), pady=(3, 0))

        # Right side: status pill + load button
        pill = ctk.CTkFrame(header, fg_color=COLORS["card"], corner_radius=13, height=26)
        self.status_dot, self.status_dot_id = self._make_dot(
            pill, COLORS["card"], STATUS_STATES["idle"]["color"])
        self.status_dot.pack(side="left", padx=(11, 6), pady=8)
        self.status_label = ctk.CTkLabel(pill, text="IDLE", font=(self.font_body, 9, "bold"),
                                         text_color=COLORS["text_dim"])
        self.status_label.pack(side="left", padx=(0, 12))
        pill.pack_propagate(False)
        pill.pack(side="right", padx=(0, 12), pady=19)

        self.btn_load = ctk.CTkButton(
            header, text="LOAD MIDI", command=self.load_midi,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            height=34, width=120, corner_radius=17, font=(self.font_body, 12, "bold"))
        self.btn_load.pack(side="right", padx=(0, 18), pady=15)
        self._bind_focus_ring(self.btn_load)

        # ── Main layout: hub sidebar + player ───────────────────
        main = ctk.CTkFrame(root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=14, pady=14)

        side = ctk.CTkScrollableFrame(
            main, fg_color="transparent", width=330,
            scrollbar_fg_color=COLORS["bg"], scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"])
        side.pack(side="left", fill="y", padx=(0, 12))

        # ── Hub sidebar: stats, searchable library, recent ──────
        stats_row = ctk.CTkFrame(side, fg_color="transparent")
        stats_row.pack(fill="x", pady=(0, 10))
        for i in range(3):
            stats_row.grid_columnconfigure(i, weight=1, uniform="hubstat")
        self.hub_stat_songs = self._hub_stat(stats_row, "SONGS", 0, size=13)
        self.hub_stat_notes = self._hub_stat(stats_row, "NOTES", 1, size=13)
        self.hub_stat_last = self._hub_stat(stats_row, "LAST", 2, size=13)

        card = self._section_card(side)
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(10, 5))
        bar = tk.Canvas(title_row, width=3, height=12, bg=COLORS["card"], highlightthickness=0)
        bar.create_rectangle(0, 0, 3, 12, fill=COLORS["accent"], outline="")
        bar.pack(side="left", padx=(0, 7))
        ctk.CTkLabel(title_row, text="LIBRARY", font=(self.font_body, 10, "bold"),
                     text_color=COLORS["text_dim"]).pack(side="left")
        self.lib_title_count = ctk.CTkLabel(title_row, text="",
                                            font=(self.font_mono, 9, "bold"),
                                            text_color=COLORS["accent_soft"])
        self.lib_title_count.pack(side="right")
        self.search_entry = ctk.CTkEntry(
            card, textvariable=self.search_var, placeholder_text="Search library…",
            height=30, corner_radius=10, fg_color=COLORS["surface"],
            border_color=COLORS["border"], font=(self.font_body, 11),
            text_color=COLORS["text"], placeholder_text_color=COLORS["text_faint"])
        self.search_entry.pack(fill="x", padx=12, pady=(0, 8))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)
        self.lib_rows = ctk.CTkScrollableFrame(
            card, fg_color=COLORS["surface"], corner_radius=10, height=180,
            scrollbar_fg_color=COLORS["surface"], scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"])
        self.lib_rows.pack(fill="x", padx=12, pady=(0, 8))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(actions, text="LOAD MIDI", command=self.load_midi, height=28,
                      corner_radius=14, fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"],
                      font=(self.font_body, 9, "bold")).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(actions, text="FOLDER", command=self._open_lib_folder, height=28,
                      corner_radius=14, fg_color=COLORS["surface"],
                      hover_color=COLORS["surface_hover"],
                      font=(self.font_body, 9, "bold")).pack(side="left", fill="x", expand=True, padx=(4, 0))

        card = self._section_card(side)
        self._section_title(card, "RECENT")
        self.recent_list = ctk.CTkScrollableFrame(
            card, fg_color=COLORS["surface"], corner_radius=10, height=96,
            scrollbar_fg_color=COLORS["surface"], scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"])
        self.recent_list.pack(fill="x", padx=12, pady=(0, 10))

        # ── CLOUD card (Railway connection) ─────────────────────
        card = self._section_card(side)
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(10, 5))
        bar = tk.Canvas(title_row, width=3, height=12, bg=COLORS["card"], highlightthickness=0)
        bar.create_rectangle(0, 0, 3, 12, fill=COLORS["action"], outline="")
        bar.pack(side="left", padx=(0, 7))
        self.cloud_dot, self.cloud_dot_id = self._make_dot(
            title_row, COLORS["card"], CLOUD_STATES["offline"]["color"])
        self.cloud_dot.pack(side="left", padx=(0, 6))
        self.cloud_label = ctk.CTkLabel(title_row, text=CLOUD_STATES["offline"]["word"],
                                        font=(self.font_body, 10, "bold"),
                                        text_color=COLORS["text_dim"])
        self.cloud_label.pack(side="left")

        self.cloud_url_entry = ctk.CTkEntry(
            card, textvariable=self.cloud_url_var,
            placeholder_text="https://your-service.up.railway.app",
            height=28, corner_radius=10, fg_color=COLORS["surface"],
            border_color=COLORS["border"], font=(self.font_mono, 9),
            text_color=COLORS["text"], placeholder_text_color=COLORS["text_faint"])
        self.cloud_url_entry.pack(fill="x", padx=12, pady=(0, 6))

        cloud_actions = ctk.CTkFrame(card, fg_color="transparent")
        cloud_actions.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_cloud_connect = ctk.CTkButton(
            cloud_actions, text="CONNECT", command=self._on_cloud_connect, height=26,
            corner_radius=13, fg_color=COLORS["action"], hover_color=COLORS["action_hover"],
            font=(self.font_body, 9, "bold"))
        self.btn_cloud_connect.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_cloud_sync = ctk.CTkButton(
            cloud_actions, text="SYNC", command=self._on_cloud_sync, height=26,
            corner_radius=13, fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_dim"], font=(self.font_body, 9, "bold"), state="disabled")
        self.btn_cloud_sync.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.btn_cloud_backup = ctk.CTkButton(
            card, text="SYNC LIBRARY", command=self._on_cloud_backup, height=26,
            corner_radius=13, fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_dim"], font=(self.font_body, 9, "bold"), state="disabled")
        self.btn_cloud_backup.pack(fill="x", padx=12, pady=(0, 4))

        self.cloud_status = ctk.CTkLabel(
            card, text="Paste your Railway URL above and hit CONNECT",
            font=(self.font_body, 9), text_color=COLORS["text_faint"],
            anchor="w", justify="left", wraplength=290)
        self.cloud_status.pack(fill="x", padx=14, pady=(0, 10))

        # ── SETTINGS card ───────────────────────────────────────
        card = self._section_card(side)
        self._section_title(card, "SETTINGS")
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 10))

        # Keyboard mode
        mode_row = ctk.CTkFrame(content, fg_color="transparent")
        mode_row.pack(fill="x", pady=4)
        ctk.CTkLabel(mode_row, text="Keyboard", font=(self.font_body, 11),
                     text_color=COLORS["text"]).pack(side="left")
        self.mode_var = ctk.StringVar(value="61")
        ctk.CTkSegmentedButton(
            mode_row, values=["61", "88"], variable=self.mode_var,
            command=self._on_mode_change, font=(self.font_body, 10, "bold"),
            selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"],
            unselected_color=COLORS["surface"], unselected_hover_color=COLORS["surface_hover"]
        ).pack(side="right")

        # Transpose
        trow = ctk.CTkFrame(content, fg_color="transparent")
        trow.pack(fill="x", pady=4)
        ctk.CTkLabel(trow, text="Transpose", font=(self.font_body, 11),
                     text_color=COLORS["text"]).pack(side="left")
        self.label_trans = ctk.CTkLabel(trow, text="0", font=(self.font_mono, 11, "bold"),
                                        text_color=COLORS["accent_soft"], width=34, anchor="e")
        self.label_trans.pack(side="right")
        self.transpose_slider = ctk.CTkSlider(
            trow, from_=-12, to=12, number_of_steps=24, command=self._on_transpose,
            width=150, height=14, fg_color=COLORS["surface"], progress_color=COLORS["accent"],
            button_color=COLORS["accent_soft"], button_hover_color=COLORS["accent_soft"])
        self.transpose_slider.set(0)
        self.transpose_slider.pack(side="right", padx=(10, 8))
        self.btn_auto = ctk.CTkButton(
            trow, text="AUTO", command=self.auto_transpose, width=46, height=22,
            corner_radius=11, fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["accent_soft"], font=(self.font_body, 9, "bold"))
        self.btn_auto.pack(side="right", padx=(8, 0))

        # Speed
        srow = ctk.CTkFrame(content, fg_color="transparent")
        srow.pack(fill="x", pady=4)
        ctk.CTkLabel(srow, text="Speed", font=(self.font_body, 11),
                     text_color=COLORS["text"]).pack(side="left")
        self.label_speed = ctk.CTkLabel(srow, text="1.00×", font=(self.font_mono, 11, "bold"),
                                        text_color=COLORS["accent_soft"], width=44, anchor="e")
        self.label_speed.pack(side="right")
        self.speed_slider = ctk.CTkSlider(
            srow, from_=0.25, to=2.0, number_of_steps=35, command=self._on_speed,
            width=150, height=14, fg_color=COLORS["surface"], progress_color=COLORS["accent"],
            button_color=COLORS["accent_soft"], button_hover_color=COLORS["accent_soft"])
        self.speed_slider.set(1.0)
        self.speed_slider.pack(side="right", padx=(10, 8))

        # Focus delay
        drow = ctk.CTkFrame(content, fg_color="transparent")
        drow.pack(fill="x", pady=4)
        ctk.CTkLabel(drow, text="Focus delay", font=(self.font_body, 11),
                     text_color=COLORS["text"]).pack(side="left")
        self.delay_var = ctk.StringVar(value="3s")
        ctk.CTkSegmentedButton(
            drow, values=["0s", "2s", "3s", "5s"], variable=self.delay_var,
            font=(self.font_body, 10, "bold"),
            selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"],
            unselected_color=COLORS["surface"], unselected_hover_color=COLORS["surface_hover"]
        ).pack(side="right")

        # Wrap octaves
        wrap_row = ctk.CTkFrame(content, fg_color="transparent")
        wrap_row.pack(fill="x", pady=(5, 0))
        self.wrap_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            wrap_row, text="Wrap notes by octave", variable=self.wrap_var,
            font=(self.font_body, 11), text_color=COLORS["text"],
            progress_color=COLORS["accent"], button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"]).pack(side="left")
        ctk.CTkLabel(content, text="Pulls out-of-range notes into reach",
                     font=(self.font_body, 9), text_color=COLORS["text_faint"],
                     anchor="w").pack(fill="x", padx=(2, 0), pady=(3, 0))

        # Loop
        loop_row = ctk.CTkFrame(content, fg_color="transparent")
        loop_row.pack(fill="x", pady=(5, 0))
        self.loop_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            loop_row, text="Loop playback", variable=self.loop_var,
            font=(self.font_body, 11), text_color=COLORS["text"],
            progress_color=COLORS["accent"], button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"]).pack(side="left")

        # ── SONG card ───────────────────────────────────────────
        card = self._section_card(side)
        self._section_title(card, "SONG")
        self.label_file = ctk.CTkLabel(card, text="No song loaded",
                                       font=(self.font_body, 13, "bold"),
                                       text_color=COLORS["text"], anchor="w", justify="left")
        self.label_file.pack(fill="x", padx=16, pady=(0, 2))
        self.label_path = ctk.CTkLabel(card, text="Load a .mid file to get started",
                                       font=(self.font_body, 10), text_color=COLORS["text_dim"],
                                       anchor="w", justify="left", wraplength=250)
        self.label_path.pack(fill="x", padx=16, pady=(0, 8))

        grid = ctk.CTkFrame(card, fg_color=COLORS["surface"], corner_radius=10)
        grid.pack(fill="x", padx=12, pady=(0, 10))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)
        self.stat_notes = self._stat_cell(grid, 0, 0, "NOTES")
        self.stat_duration = self._stat_cell(grid, 0, 1, "DURATION")
        self.stat_tracks = self._stat_cell(grid, 1, 0, "TRACKS")
        self.stat_keys = self._stat_cell(grid, 1, 1, "KEYS")
        self.stat_keys.configure(text="61")

        # ── TRACKS card ─────────────────────────────────────────
        card = self._section_card(side)
        self._section_title(card, "TRACKS")
        master_row = ctk.CTkFrame(card, fg_color="transparent")
        master_row.pack(fill="x", padx=16, pady=(0, 6))
        self.master_var = ctk.BooleanVar(value=True)
        self.master_switch = ctk.CTkSwitch(
            master_row, text="All tracks", variable=self.master_var,
            command=self._on_master_toggle, font=(self.font_body, 10),
            progress_color=COLORS["accent"], button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"])
        self.master_switch.pack(side="left")

        self.track_frame = ctk.CTkScrollableFrame(
            card, fg_color=COLORS["surface"], corner_radius=10, height=104,
            scrollbar_fg_color=COLORS["surface"], scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"])
        self.track_frame.pack(fill="x", padx=12, pady=(0, 10))
        self.label_tracks_empty = ctk.CTkLabel(
            self.track_frame, text="No track data — load a MIDI",
            font=(self.font_body, 10), text_color=COLORS["text_faint"])
        self.label_tracks_empty.pack(padx=8, pady=8)

        # ── Center column ───────────────────────────────────────
        center = ctk.CTkFrame(main, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True)

        # Visualizer — falling notes above the piano keys
        self.fall_h = 190
        viz_card = ctk.CTkFrame(center, fg_color=COLORS["card"], corner_radius=12, height=330)
        viz_card.pack(fill="x", pady=(0, 12))
        viz_card.pack_propagate(False)
        self._section_title(viz_card, "VISUALIZER")
        self.canvas = tk.Canvas(viz_card, bg=COLORS["card"], highlightthickness=0, height=286)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.visual_piano = VisualPiano(self.canvas, mode=self.mode, y_offset=self.fall_h)

        # Transport
        card = ctk.CTkFrame(center, fg_color=COLORS["card"], corner_radius=12)
        card.pack(fill="both", expand=True)

        np_row = ctk.CTkFrame(card, fg_color="transparent")
        np_row.pack(fill="x", padx=18, pady=(16, 0))
        self.label_now = ctk.CTkLabel(np_row, text="No song loaded",
                                      font=(self.font_body, 15, "bold"),
                                      text_color=COLORS["text"], anchor="w")
        self.label_now.pack(side="left", fill="x", expand=True)
        self.label_elapsed = ctk.CTkLabel(np_row, text="0:00", font=(self.font_mono, 13, "bold"),
                                          text_color=COLORS["text"], width=46, anchor="e")
        self.label_elapsed.pack(side="right")
        ctk.CTkLabel(np_row, text="/", font=(self.font_mono, 12),
                     text_color=COLORS["text_faint"]).pack(side="right", padx=4)
        self.label_total = ctk.CTkLabel(np_row, text="0:00", font=(self.font_mono, 13, "bold"),
                                        text_color=COLORS["text_dim"], width=46, anchor="w")
        self.label_total.pack(side="right")

        # Progress slot — the big countdown numeral swaps in here during the
        # focus delay, so nothing below shifts (motion-12).
        prog_row = ctk.CTkFrame(card, fg_color="transparent", height=36)
        prog_row.pack(fill="x", padx=18, pady=(10, 4))
        prog_row.pack_propagate(False)
        self.progress = ctk.CTkProgressBar(
            prog_row, progress_color=COLORS["action"], height=8, corner_radius=4,
            fg_color=COLORS["surface"])
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, pady=14)
        self.label_count = ctk.CTkLabel(prog_row, text="3",
                                        font=(self.font_mono, 26, "bold"),
                                        text_color=COLORS["warn"], width=120)
        self.label_count.pack_forget()

        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=18, pady=(0, 0))
        self.transport_dot, self.transport_dot_id = self._make_dot(
            status_row, COLORS["card"], STATUS_STATES["idle"]["color"])
        self.transport_dot.pack(side="left", padx=(0, 8), pady=3)
        self.label_status = ctk.CTkLabel(status_row,
                                         text="Load a .mid, press PLAY, then switch to Roblox",
                                         font=(self.font_body, 11), text_color=COLORS["text_dim"],
                                         anchor="w")
        self.label_status.pack(side="left", fill="x", expand=True)

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(pady=(16, 8))
        self.btn_play = ctk.CTkButton(
            ctrl, text="PLAY", command=self.play,
            fg_color=COLORS["action"], hover_color=COLORS["action_hover"],
            text_color=COLORS["action_text"],
            width=170, height=46, corner_radius=23, font=(self.font_body, 14, "bold"))
        self.btn_play.pack(side="left", padx=8)
        self.btn_pause = ctk.CTkButton(
            ctrl, text="PAUSE", command=self.pause,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            width=110, height=46, corner_radius=12, font=(self.font_body, 12, "bold"))
        self.btn_pause.pack(side="left", padx=8)
        self.btn_stop = ctk.CTkButton(
            ctrl, text="STOP", command=self.stop,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            width=110, height=46, corner_radius=12, font=(self.font_body, 12, "bold"))
        self.btn_stop.pack(side="left", padx=8)
        for w in (self.btn_play, self.btn_pause, self.btn_stop):
            self._bind_focus_ring(w)

        hint_row = ctk.CTkFrame(card, fg_color="transparent")
        hint_row.pack(fill="x", padx=18, pady=(6, 16))
        h1 = ctk.CTkFrame(hint_row, fg_color="transparent")
        h1.pack(side="left", padx=(0, 16))
        self._keycap(h1, "F6")
        ctk.CTkLabel(h1, text="Play / Pause", font=(self.font_body, 9),
                     text_color=COLORS["text_faint"]).pack(side="left", pady=3)
        h2 = ctk.CTkFrame(hint_row, fg_color="transparent")
        h2.pack(side="left", padx=(0, 16))
        self._keycap(h2, "F7")
        ctk.CTkLabel(h2, text="Stop", font=(self.font_body, 9),
                     text_color=COLORS["text_faint"]).pack(side="left", pady=3)
        h3 = ctk.CTkFrame(hint_row, fg_color="transparent")
        h3.pack(side="left", padx=(0, 16))
        self._keycap(h3, "Ctrl+O")
        ctk.CTkLabel(h3, text="Load MIDI", font=(self.font_body, 9),
                     text_color=COLORS["text_faint"]).pack(side="left", pady=3)
        h4 = ctk.CTkFrame(hint_row, fg_color="transparent")
        h4.pack(side="left")
        self._keycap(h4, "Space")
        ctk.CTkLabel(h4, text="Play / Pause", font=(self.font_body, 9),
                     text_color=COLORS["text_faint"]).pack(side="left", pady=3)
        ctk.CTkLabel(hint_row, text="Switch to Roblox during the countdown",
                     font=(self.font_body, 9), text_color=COLORS["text_faint"]).pack(side="right", pady=3)

        # ── Footer ──────────────────────────────────────────────
        bottom = ctk.CTkFrame(root, fg_color=COLORS["surface"], corner_radius=0, height=30)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        ctk.CTkLabel(bottom, text="For educational & entertainment use only",
                     font=(self.font_body, 9), text_color=COLORS["text_faint"]).pack(side="left", padx=16, pady=7)
        ctk.CTkLabel(bottom, text=f"KeyPrism · falling notes · loop · auto-transpose · v{VERSION}",
                     font=(self.font_body, 9), text_color=COLORS["text_faint"]).pack(side="right", padx=16, pady=7)

        # Initial piano draw once layout settles
        self.root.after(150, lambda: self.visual_piano.draw())

        self._scan_library()

    def _build_tk_ui(self):
        """Fallback classic-tkinter UI, restyled to the same tokens."""
        self.root.geometry("960x640")
        bg = COLORS["bg"]
        frame = tk.Frame(self.root, bg=bg)
        frame.pack(fill="both", expand=True, padx=14, pady=14)

        header = tk.Frame(frame, bg=COLORS["surface"])
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text="KEYPRISM", font=(self.font_brand, 18, "bold"),
                 bg=COLORS["surface"], fg=COLORS["text"]).pack(side="left", padx=14, pady=10)
        tk.Label(header, text="Roblox Piano Auto", font=(self.font_body, 10),
                 bg=COLORS["surface"], fg=COLORS["text_dim"]).pack(side="left")

        self.label_file = tk.Label(frame, text="No file loaded", bg=bg, fg=COLORS["text"],
                                   anchor="w", font=(self.font_body, 13, "bold"))
        self.label_file.pack(fill="x", pady=(0, 2))
        self.label_stats = tk.Label(frame, text="Load a .mid to begin", bg=bg,
                                    fg=COLORS["text_dim"], anchor="w", justify="left",
                                    font=(self.font_mono, 10))
        self.label_stats.pack(fill="x", pady=(0, 8))

        btn_row = tk.Frame(frame, bg=bg)
        btn_row.pack(fill="x", pady=(0, 8))
        tk.Button(btn_row, text="Load MIDI", command=self.load_midi, bg=COLORS["accent"],
                  activebackground=COLORS["accent_hover"], fg="white",
                  font=(self.font_body, 11, "bold"), relief="flat", padx=14, pady=6,
                  cursor="hand2").pack(side="left", padx=(0, 6))
        self.btn_play = tk.Button(btn_row, text="PLAY", command=self.play, bg=COLORS["action"],
                                  activebackground=COLORS["action_hover"], fg="white",
                                  font=(self.font_body, 12, "bold"), relief="flat", padx=18,
                                  pady=6, cursor="hand2")
        self.btn_play.pack(side="left", padx=6)
        tk.Button(btn_row, text="PAUSE", command=self.pause, bg=COLORS["surface"],
                  activebackground=COLORS["surface_hover"], fg=COLORS["text"], relief="flat",
                  padx=12, pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_row, text="STOP", command=self.stop, bg=COLORS["surface"],
                  activebackground=COLORS["surface_hover"], fg=COLORS["text"], relief="flat",
                  padx=12, pady=6, cursor="hand2").pack(side="left", padx=6)

        self.canvas = tk.Canvas(frame, bg=COLORS["card"], height=120, highlightthickness=0)
        self.canvas.pack(fill="x", pady=(0, 8))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.visual_piano = VisualPiano(self.canvas, mode=self.mode)

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1000)
        self.progress.pack(fill="x", pady=(0, 8))

        self.label_status = tk.Label(frame, text="Ready — load a MIDI and press Play",
                                     bg=bg, fg=COLORS["text_dim"], anchor="w",
                                     font=(self.font_body, 10))
        self.label_status.pack(fill="x", pady=(0, 8))

        ctrl = tk.Frame(frame, bg=bg)
        ctrl.pack(fill="x")
        tk.Label(ctrl, text="Transpose", bg=bg, fg=COLORS["text"],
                 font=(self.font_body, 10)).pack(side="left")
        self.transpose_slider = tk.Scale(ctrl, from_=-12, to=12, orient="horizontal",
                                         command=lambda v: self._on_transpose(float(v)),
                                         bg=bg, fg=COLORS["text"], highlightthickness=0,
                                         troughcolor=COLORS["surface"],
                                         activebackground=COLORS["accent"])
        self.transpose_slider.set(0)
        self.transpose_slider.pack(side="left", padx=8)
        self.label_trans = tk.Label(ctrl, text="0", bg=bg, fg=COLORS["accent_soft"],
                                    font=(self.font_mono, 10, "bold"))
        self.label_trans.pack(side="left")

        tk.Label(ctrl, text="  Speed", bg=bg, fg=COLORS["text"],
                 font=(self.font_body, 10)).pack(side="left")
        self.speed_slider = tk.Scale(ctrl, from_=0.25, to=2.0, resolution=0.05,
                                     orient="horizontal",
                                     command=lambda v: self._on_speed(float(v)),
                                     bg=bg, fg=COLORS["text"], highlightthickness=0,
                                     troughcolor=COLORS["surface"],
                                     activebackground=COLORS["accent"])
        self.speed_slider.set(1.0)
        self.speed_slider.pack(side="left", padx=8)
        self.label_speed = tk.Label(ctrl, text="1.00×", bg=bg, fg=COLORS["accent_soft"],
                                    font=(self.font_mono, 10, "bold"))
        self.label_speed.pack(side="left")

        opt_row = tk.Frame(frame, bg=bg)
        opt_row.pack(fill="x", pady=(8, 0))
        self.mode_var = tk.StringVar(value="61")
        self.delay_var = tk.StringVar(value="3s")
        self.wrap_var = tk.BooleanVar(value=True)
        tk.Label(opt_row, text="Mode", bg=bg, fg=COLORS["text"],
                 font=(self.font_body, 10)).pack(side="left")
        tk.OptionMenu(opt_row, self.mode_var, "61", "88",
                      command=self._on_mode_change).pack(side="left", padx=(6, 16))
        tk.Label(opt_row, text="Focus delay", bg=bg, fg=COLORS["text"],
                 font=(self.font_body, 10)).pack(side="left")
        tk.OptionMenu(opt_row, self.delay_var, "0s", "2s", "3s", "5s").pack(side="left", padx=(6, 16))
        tk.Checkbutton(opt_row, text="Wrap octaves", variable=self.wrap_var, bg=bg,
                       fg=COLORS["text"], selectcolor=COLORS["surface"],
                       activebackground=bg, activeforeground=COLORS["text"],
                       font=(self.font_body, 10)).pack(side="left")

        self.track_frame = tk.Frame(frame, bg=COLORS["surface"])

    # ── events / wiring ──────────────────────────────────────────

    def _bind_hotkeys(self):
        try:
            self.root.bind("<F6>", lambda e: self.toggle_play())
            self.root.bind("<F7>", lambda e: self.stop())
            self.root.bind("<Control-o>", lambda e: self.load_midi())
            self.root.bind("<space>", self._on_space)
        except Exception:
            pass

    def _on_space(self, event):
        # Space = Play/Pause everywhere except while typing in a field
        try:
            w = self.root.focus_get()
            if w is not None and w.winfo_class() in ("Entry", "TEntry", "Text", "TSpinbox"):
                return
        except Exception:
            pass
        self.toggle_play()
        return "break"

    def _on_canvas_resize(self, event):
        if event.width < 80 or event.height < 30:
            return
        if hasattr(self, "_resize_job"):
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(
            100, lambda: self.visual_piano.draw(width=event.width, height=event.height))

    def _on_mode_change(self, value):
        self.mode = str(value)
        if hasattr(self, "mode_var"):
            try:
                self.mode_var.set(self.mode)
            except Exception:
                pass
        self.visual_piano.mode = self.mode
        self.visual_piano.draw()
        if hasattr(self, "stat_keys"):
            try:
                self.stat_keys.configure(text=self.mode)
            except Exception:
                pass
        if self.parser:
            self._reparse()

    def _on_transpose(self, value):
        self.transpose = int(float(value))
        if hasattr(self, "label_trans"):
            try:
                self.label_trans.configure(text=f"{self.transpose:+d}")
            except Exception:
                pass
        if self.parser and self.events:
            self._reparse()

    def _on_speed(self, value):
        self.speed = float(value)
        if hasattr(self, "label_speed"):
            try:
                self.label_speed.configure(text=f"{self.speed:.2f}×")
            except Exception:
                pass

    def _on_master_toggle(self):
        on = self.master_var.get()
        for _idx, var in getattr(self, "track_vars", []):
            var.set(on)
        if hasattr(self, "track_vars"):
            self.enabled_tracks = None if on else []
            self._reparse()

    def load_midi(self):
        path = filedialog.askopenfilename(
            title="Select MIDI file",
            initialdir="midi_files",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All", "*.*")]
        )
        if not path:
            return
        self.load_midi_path(path)

    def load_midi_path(self, path):
        self._set_status("Loading MIDI…", state="loading")
        # Indeterminate progress while the file parses (motion-23)
        if USE_CTK:
            try:
                self.progress.configure(mode="indeterminate")
                self.progress.start()
            except Exception:
                pass
        try:
            self.current_file = path
            self.parser = MidiParser(path)
            track_info = self.parser.get_track_info()

            name = Path(path).name
            if USE_CTK:
                self.label_file.configure(text=name)
                self.label_path.configure(text=self._truncate_path(path))
                self.label_now.configure(text=name)
            else:
                self.label_file.config(text=name)
                if hasattr(self, "label_path"):
                    self.label_path.config(text=str(Path(path)))

            self._populate_tracks(track_info)
            self._reparse()

            self._record_recent(path)
            self._set_status(f"Loaded {name} · {self._plural(len(track_info), 'track')}",
                             state="loaded")
            if hasattr(self, "hub_stat_songs"):
                self._render_hub_stats()
                self._render_recent()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load MIDI:\n{e}")
            self._set_status(f"Error: {e}", state="error")
        finally:
            if USE_CTK:
                try:
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(0)
                except Exception:
                    pass

    def _populate_tracks(self, track_info):
        if USE_CTK:
            for w in self.track_frame.winfo_children():
                w.destroy()
            self.track_vars = []
            for info in track_info:
                var = ctk.BooleanVar(value=True)
                self.track_vars.append((info["index"], var))
                row = ctk.CTkFrame(self.track_frame, fg_color="transparent")
                row.pack(fill="x", padx=6, pady=2)
                name = info["name"] or f"Track {info['index'] + 1}"
                if len(name) > 26:
                    name = name[:24] + "…"
                ctk.CTkCheckBox(
                    row, text=name, variable=var, command=self._on_track_toggle,
                    font=(self.font_body, 10), text_color=COLORS["text"],
                    checkbox_width=18, checkbox_height=18, border_color=COLORS["border"],
                    hover_color=COLORS["surface"], fg_color=COLORS["accent"]
                ).pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(row, text=f"{info['notes']}", font=(self.font_mono, 9),
                             text_color=COLORS["text_faint"]).pack(side="right", padx=(6, 4))
            self.master_var.set(True)
        else:
            # Tk fallback — keep track info for filtering
            self.track_vars = [(i["index"], None) for i in track_info]

    def _on_track_toggle(self):
        if not hasattr(self, "track_vars") or not self.track_vars:
            return
        enabled = [idx for idx, var in self.track_vars if var.get()]
        self.enabled_tracks = enabled if len(enabled) != len(self.track_vars) else None
        if hasattr(self, "master_var"):
            self.master_var.set(len(enabled) == len(self.track_vars))
        self._reparse()

    def _reparse(self):
        if not self.parser:
            return
        try:
            mode = self.mode_var.get() if hasattr(self.mode_var, "get") else self.mode
            wrap = self.wrap_var.get() if hasattr(self.wrap_var, "get") else self.wrap_octave

            events = self.parser.parse(
                transpose=self.transpose,
                mode=mode,
                enabled_tracks=self.enabled_tracks,
                wrap_octave=wrap
            )
            self.events = events
            stats = self.parser.get_stats(events)
            self.total_time = stats.get("duration", 0)

            if USE_CTK:
                self.stat_notes.configure(text=f"{stats['notes']:,}")
                self.stat_duration.configure(text=fmt_time(stats["duration"]))
                self.stat_tracks.configure(text=str(stats.get("tracks", self.parser.tracks)))
                self.stat_keys.configure(text=mode)
                self.label_total.configure(text=fmt_time(self.total_time))
                self.label_elapsed.configure(text="0:00")
            else:
                stats_text = (f"Notes: {stats['notes']:,} | Duration: {fmt_time(stats['duration'])} "
                              f"| Tracks: {self.parser.tracks} | Mode: {mode}-keys | "
                              f"Wrap: {'ON' if wrap else 'OFF'}")
                self.label_stats.config(text=stats_text)
        except Exception as e:
            self._set_status(f"Parse error: {e}", state="error")

    def _on_note_played(self, char: str):
        # Called from player thread — hand off to the UI thread
        self._push("note", char)

    # ── falling notes (Synthesia-style) ──────────────────────────

    def _clock_now(self):
        if self._last_t is None:
            return 0.0
        if not self.player._pause_flag.is_set():
            return self._last_t
        return self._last_t + (time.time() - self._last_cb) * self.speed

    def _anim_loop(self):
        try:
            self._anim_frame()
        except Exception:
            pass
        self.root.after(16, self._anim_loop)

    def _anim_frame(self):
        c = self.canvas
        c.delete("fall")
        if self._reduced or not self.player.is_playing or not self.events:
            return
        if self.status_state == "countdown":
            return
        clock = self._clock_now()
        max_t = self.events[-1].time
        if clock > max_t:
            if not (self.loop_var.get() if hasattr(self, "loop_var") else False):
                return
            clock = 0.0
        win = 2.6
        key_x = getattr(self.visual_piano, "key_x", {})
        if not key_x:
            return
        for ev in self.events:
            dt = ev.time - clock
            if 0 <= dt <= win:
                x = key_x.get(ev.key_char)
                if x is None:
                    continue
                y = self.fall_h - 10 - (dt / win) * (self.fall_h - 44)
                col = COLORS["action"] if ev.velocity > 80 else COLORS["accent_soft"]
                c.create_rectangle(x - 9, y - 16, x + 9, y, fill=col, outline="", tags="fall")

    # ── Cloud (Railway) ─────────────────────────────────────────

    def _cloud_state(self, state, text=None):
        st = CLOUD_STATES.get(state, CLOUD_STATES["offline"])
        try:
            self._set_dot(self.cloud_dot, self.cloud_dot_id, st["color"])
            self.cloud_label.configure(text=st["word"])
            if text is not None:
                self.cloud_status.configure(text=text)
        except Exception:
            pass

    def _cloud_auto_connect(self):
        """Auto-connect to cloud on startup if a URL is saved."""
        url = self.cloud_url_var.get().strip()
        if url and not self.cloud_online:
            self._on_cloud_connect(cold_start=True)

    def _on_cloud_connect(self, cold_start=False):
        if self.cloud_online:
            self.cloud_online = False
            self.cloud_client = None
            self._cloud_state("offline", "Disconnected — paste your Railway URL to reconnect")
            self.btn_cloud_connect.configure(text="CONNECT")
            self.btn_cloud_sync.configure(state="disabled")
            self.btn_cloud_backup.configure(state="disabled")
            return
        url = self.cloud_url_var.get().strip()
        if not url:
            self._cloud_state("error", "Enter your Railway URL first (https://…up.railway.app)")
            return
        self.history["cloud_url"] = url
        self._save_history()
        hint = " (Railway cold start — may take 30s)" if cold_start else ""
        self._cloud_state("connecting", f"Contacting{hint} …")
        self.btn_cloud_connect.configure(state="disabled")
        threading.Thread(target=self._cloud_connect_worker,
                         args=(url, cold_start), daemon=True).start()

    def _cloud_connect_worker(self, url, cold_start=False):
        timeout = 30.0 if cold_start else 8.0
        try:
            client = CloudClient(url, timeout=timeout)
            info = client.health()
            if info.get("status") != "ok":
                raise CloudError("service reported non-ok status")
            remote = client.get_stats()
            st = self.history.setdefault("stats", {})
            st["songs_played"] = max(st.get("songs_played", 0), remote.get("songs_played", 0))
            st["notes_played"] = max(st.get("notes_played", 0), remote.get("notes_played", 0))
            client.push_stats(st.get("songs_played", 0), st.get("notes_played", 0))
            self._save_history()
            self._push("cloud_connected", url, st.get("songs_played", 0),
                       st.get("notes_played", 0))
        except CloudError as e:
            err = str(e)
            if "timed out" in err.lower() or "timeout" in err.lower():
                hint = "Railway free tier cold starts take 30-60s — try again in a minute."
                self._push("cloud_error", f"Server is waking up: {hint}")
            elif "404" in err:
                self._push("cloud_error", "URL not found — check your Railway service URL")
            elif "refused" in err.lower():
                self._push("cloud_error", "Connection refused — server may be starting up")
            else:
                self._push("cloud_error", f"Connection failed: {err}")

    def _on_cloud_sync(self):
        if not self.cloud_online:
            return
        self._cloud_state("syncing", "Syncing stats …")
        self.btn_cloud_sync.configure(state="disabled")
        threading.Thread(target=self._cloud_sync_worker, daemon=True).start()

    def _cloud_sync_worker(self):
        try:
            st = self.history.setdefault("stats", {})
            remote = self.cloud_client.push_stats(
                st.get("songs_played", 0), st.get("notes_played", 0))
            self._push("cloud_synced", remote)
        except CloudError as e:
            self._push("cloud_error", str(e))

    def _on_cloud_backup(self):
        if not self.cloud_online:
            return
        self._cloud_state("syncing", "Syncing library …")
        self.btn_cloud_backup.configure(state="disabled")
        threading.Thread(target=self._cloud_backup_worker, daemon=True).start()

    def _cloud_backup_worker(self):
        """Two-way library sync: upload local MIDIs, pull remote ones we lack."""
        try:
            client = self.cloud_client
            uploaded = 0
            for path in sorted(Path("midi_files").glob("*.mid*")):
                meta = self._lib_cache.get(str(path), {})
                try:
                    client.upload_song(path.name, path.read_bytes(), {
                        "notes": meta.get("notes", 0),
                        "duration": meta.get("duration", 0),
                        "tracks": meta.get("tracks", 0)})
                    uploaded += 1
                except CloudError:
                    continue
            remote = client.list_library()
            local_names = {p.name for p in Path("midi_files").glob("*.mid*")}
            fetched = 0
            for song in remote:
                if song.get("name") in local_names:
                    continue
                try:
                    data = client.download_song(song["id"])
                    (Path("midi_files") / song["name"]).write_bytes(data)
                    fetched += 1
                except Exception:
                    pass
            self._push("cloud_library_done", uploaded, fetched)
        except CloudError as e:
            self._push("cloud_error", str(e))

    def _cloud_push_stats(self):
        """Fire-and-forget stat push after a song finishes (if online)."""
        if not self.cloud_online or self.cloud_client is None:
            return
        threading.Thread(
            target=lambda: self._cloud_sync_worker(), daemon=True).start()

    def _cloud_keepalive(self):
        """Background thread: ping /api/health every 60s, reconnect if dropped."""
        while True:
            time.sleep(60)
            if not self.cloud_online or self.cloud_client is None:
                continue
            try:
                info = self.cloud_client.health()
                if info.get("status") != "ok":
                    raise CloudError("unhealthy")
            except CloudError:
                self.cloud_online = False
                self._push("cloud_error", "Connection lost — reconnecting…")
                # Auto-reconnect after a short delay
                time.sleep(3)
                self.root.after(0, self._cloud_auto_connect)

    def _on_cloud_url_validate(self, event=None):
        """Validate URL when the user tabs/clicks out of the entry."""
        url = self.cloud_url_var.get().strip()
        if not url or self.cloud_online:
            return
        # Quick ping to show the user whether the URL works
        self._cloud_state("connecting", "Validating URL…")
        threading.Thread(target=self._cloud_validate_worker, args=(url,), daemon=True).start()

    def _cloud_validate_worker(self, url):
        try:
            client = CloudClient(url, timeout=10.0)
            info = client.health()
            if info.get("status") == "ok":
                self._push("cloud_url_valid", url)
            else:
                self._push("cloud_error", "Server responded but is not healthy")
        except CloudError as e:
            self._push("cloud_error", f"Cannot reach server: {e}")

    # ── library + auto-transpose ─────────────────────────────────

    def auto_transpose(self):
        """Center the song's median note in the keyboard range (auto-best transpose)."""
        if not self.parser:
            self._set_status("Load a MIDI first — nothing to auto-transpose", state="error")
            return
        mode = self.mode_var.get() if hasattr(self.mode_var, "get") else self.mode
        enabled = self.enabled_tracks
        base, top = (36, 96) if mode == "61" else (21, 108)
        notes = []
        for ti, tr in enumerate(self.parser.midi.tracks):
            if enabled is not None and ti not in enabled:
                continue
            for m in tr:
                if m.type == "note_on" and m.velocity > 0 and getattr(m, "channel", 0) != 9:
                    notes.append(m.note)
        if not notes:
            self._set_status("No notes found to transpose", state="error")
            return
        notes.sort()
        median = notes[len(notes) // 2]
        t = int(round((base + top) / 2 - median))
        t = max(-24, min(24, t))
        self.transpose = t
        if hasattr(self, "transpose_slider"):
            self.transpose_slider.set(t)
        self._reparse()
        self._set_status(f"Auto transpose {t:+d} — song centered on note {median}", state="loaded")

    # ── Hub sidebar ─────────────────────────────────────────────

    def _hub_stat(self, parent, caption, index, size=20):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12)
        padx = (0, 8) if index == 0 else (8, 8) if index == 1 else (8, 0)
        card.grid(row=0, column=index, sticky="nsew", padx=padx)
        value = ctk.CTkLabel(card, text="—", font=(self.font_mono, size, "bold"),
                             text_color=COLORS["text"], anchor="w")
        value.pack(anchor="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(card, text=caption, font=(self.font_body, 8, "bold"),
                     text_color=COLORS["text_faint"], anchor="w").pack(anchor="w", padx=12, pady=(0, 10))
        return value

    def _render_hub_stats(self):
        if not hasattr(self, "hub_stat_songs"):
            return
        total_notes = sum(e.get("notes", 0) for e in self._lib_cache.values())
        recent = self.history.get("recent", [])
        last = Path(recent[0]).name if recent else "—"
        if len(last) > 16:
            last = last[:14] + "…"
        self.hub_stat_songs.configure(text=f"{len(self._lib_cache):,}")
        self.hub_stat_notes.configure(text=f"{total_notes:,}")
        self.hub_stat_last.configure(text=last)
        if hasattr(self, "lib_title_count"):
            self.lib_title_count.configure(text=f"{len(self._lib_cache)}")

    def _render_recent(self):
        if not hasattr(self, "recent_list"):
            return
        for w in self.recent_list.winfo_children():
            w.destroy()
        recent = self.history.get("recent", [])
        if not recent:
            ctk.CTkLabel(self.recent_list, text="Nothing played yet — load a song from the library",
                         font=(self.font_body, 10), text_color=COLORS["text_faint"]).pack(padx=8, pady=10)
            return
        for path in recent[:RECENT_MAX]:
            row = ctk.CTkFrame(self.recent_list, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=2)
            name = Path(path).name
            if len(name) > 40:
                name = name[:38] + "…"
            ctk.CTkLabel(row, text=name, font=(self.font_body, 10),
                         text_color=COLORS["text"], anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="PLAY", width=46, height=22, corner_radius=11,
                          fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                          font=(self.font_body, 8, "bold"),
                          command=lambda p=path: self._load_from_hub(p)).pack(side="right")

    def _render_lib(self):
        if not hasattr(self, "lib_rows"):
            return
        for w in self.lib_rows.winfo_children():
            w.destroy()
        query = self.search_var.get().strip().lower()
        paths = sorted(self._lib_cache)
        if query:
            paths = [p for p in paths if query in Path(p).name.lower()]
        if not paths:
            msg = "No matches" if query else "Scanning library… add .mid files to midi_files"
            ctk.CTkLabel(self.lib_rows, text=msg, font=(self.font_body, 10),
                         text_color=COLORS["text_faint"]).pack(padx=8, pady=10)
            return
        for path in paths:
            self._lib_row(path, self._lib_cache[path])

    def _lib_row(self, path, entry):
        row = ctk.CTkFrame(self.lib_rows, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)
        ctk.CTkButton(row, text="PLAY", width=46, height=22, corner_radius=11,
                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                      font=(self.font_body, 8, "bold"),
                      command=lambda p=path: self._load_from_hub(p)).pack(side="left", padx=(0, 8))
        name = Path(path).name
        if len(name) > 44:
            name = name[:42] + "…"
        ctk.CTkLabel(row, text=name, font=(self.font_body, 10),
                     text_color=COLORS["text"], anchor="w").pack(side="left", fill="x", expand=True)
        meta = f"{entry.get('notes', 0):,} notes · {fmt_time(entry.get('duration', 0))}"
        ctk.CTkLabel(row, text=meta, font=(self.font_mono, 9),
                     text_color=COLORS["text_faint"]).pack(side="right", padx=(6, 4))

    def _load_history(self):
        try:
            self.history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(self.history, dict):
                raise ValueError("bad history")
        except Exception:
            self.history = {"recent": [], "stats": {"songs_played": 0, "notes_played": 0}, "songs": {}}
        self.history.setdefault("recent", [])
        self.history.setdefault("stats", {"songs_played": 0, "notes_played": 0})
        self.history.setdefault("songs", {})
        self._lib_cache = self.history["songs"]

    def _save_history(self):
        try:
            self.history["songs"] = self._lib_cache
            HISTORY_FILE.write_text(json.dumps(self.history, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _record_recent(self, path):
        recent = self.history.setdefault("recent", [])
        key = str(path)
        if key in recent:
            recent.remove(key)
        recent.insert(0, key)
        del recent[RECENT_MAX:]
        self._save_history()

    def _scan_library(self):
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._scan_thread = threading.Thread(target=self._scan_library_worker, daemon=True)
        self._scan_thread.start()

    def _scan_library_worker(self):
        try:
            for p in sorted(Path("midi_files").glob("*.mid*")):
                key = str(p)
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    continue
                cached = self._lib_cache.get(key)
                if cached and abs(cached.get("mtime", 0) - mtime) < 1.0:
                    continue
                try:
                    parser = MidiParser(str(p))
                    info = parser.get_track_info()
                    events = parser.parse(mode="61", wrap_octave=True)
                    stats = parser.get_stats(events)
                    entry = {"notes": stats["notes"], "duration": stats.get("duration", 0),
                             "tracks": len(info), "mtime": mtime}
                    self._lib_cache[key] = entry
                    self._push("lib_item", key, entry)
                except Exception:
                    pass
        finally:
            self._push("lib_done")

    def _on_search_change(self, event=None):
        if self._search_job is not None:
            try:
                self.root.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.root.after(150, self._render_lib)

    def _open_lib_folder(self):
        folder = Path("midi_files").resolve()
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(folder))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            self._set_status(f"Could not open folder: {e}", state="error")

    def _load_from_hub(self, path):
        self.load_midi_path(path)

    def play(self):
        if not self.events:
            # Acknowledge the dead end instead of blocking (motion-16/24)
            self._flash_button(self.btn_play)
            self._set_status("Load a .mid file first — nothing to play yet", state="error")
            return
        if self.player.is_playing:
            self._sink_progress = False
            self.player.resume()
            self.btn_play.configure(text="PLAY")
            self._set_status("Resumed", state="playing")
            return

        speed = self.speed
        delay_str = self.delay_var.get() if hasattr(self.delay_var, "get") else "3s"
        try:
            delay = int(delay_str.replace("s", ""))
        except Exception:
            delay = 3

        def on_progress(current, total, t):
            # Runs on the player thread — only queue messages
            if t < 0:
                n = -int(t)
                self._push("countdown",
                           f"Switch to Roblox — keys start in {n}s", n)
            else:
                pct = current / total if total else 0
                name = Path(self.current_file).name if self.current_file else ""
                if current >= total:
                    self._push("progress", 1.0, fmt_time(self.total_time), t,
                               f"Finished · {self._plural(total, 'note')} played", "finished")
                    self._push("played", total)
                else:
                    self._push("progress", pct, fmt_time(t), t,
                               f"Playing {current}/{total} · {name}", "playing")

        self._sink_progress = False
        loop = self.loop_var.get() if hasattr(self, "loop_var") else False
        self.player.play(self.events, speed=speed, focus_delay=delay,
                         loop=loop, on_progress=on_progress)
        self._set_status(f"Starting in {delay}s — FOCUS ROBLOX PIANO NOW!", state="countdown")

    def pause(self):
        if self.player.is_playing:
            if self.player._pause_flag.is_set():
                self.player.pause()
                self.btn_play.configure(text="RESUME")
                self._set_status("Paused — press PLAY or F6 to resume", state="paused")
            else:
                self.player.resume()
                self.btn_play.configure(text="PLAY")
                self._set_status("Resumed", state="playing")

    def toggle_play(self):
        if self.player.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self):
        self._sink_progress = True
        self.player.stop()
        self._set_countdown_visible(False)
        if self._count_flash_job is not None:
            try:
                self.root.after_cancel(self._count_flash_job)
            except Exception:
                pass
            self._count_flash_job = None
            if hasattr(self, "label_count"):
                try:
                    self.label_count.configure(text_color=COLORS["warn"])
                except Exception:
                    pass
        if USE_CTK:
            self.progress.set(0)
            self.label_elapsed.configure(text="0:00")
        else:
            try:
                self.progress["value"] = 0
            except Exception:
                pass
        self.btn_play.configure(text="PLAY")
        self._set_status("Stopped", state="stopped")

    def run(self):
        self.root.mainloop()


def main():
    # Check for dependencies
    try:
        import mido
    except ImportError:
        print("Installing dependencies...")
        os.system(f"{sys.executable} -m pip install mido pynput pyautogui customtkinter")

    app = NanoApp()
    app.run()


if __name__ == "__main__":
    main()
