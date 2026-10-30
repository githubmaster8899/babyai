"""Floating desktop overlay for BabyAI (Tkinter).

A borderless, always-on-top assistant silhouette with one compact speech panel
(cut corners, neon edge, status tag, inline input) and a minimal control row:
pencil = type a message, mic = voice input, chip = launch the separate Capri
app (see babyai.capri_launcher).

Transparency depends on the platform / compositor:

* Windows and macOS: the window background is keyed out, so only the figure,
  panel and buttons are visible - there is no app frame at all.
* X11 / Wayland (via XWayland), i.e. most Linux desktops: Tk cannot do per-pixel
  transparency, so the overlay uses a semi-transparent dark panel instead. The
  alpha needs a running compositor; without one the panel is simply opaque dark,
  never a white or grey box.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the UI only relies on the assistant's public methods at runtime
    from babyai.agent.core import BabyAI

WIDTH, HEIGHT = 560, 640

KEY_COLOR = "#010203"  # keyed out to transparent on Windows (not used elsewhere)
PANEL_BG = "#080d13"  # window fallback when real transparency is unavailable
PANEL_ALPHA = 0.88

# speech panel
PANEL_FILL = "#2a1f34"
PANEL_EDGE = "#8a52e8"
TILE_FILL = "#3a2753"
FIELD_BG = "#4a3d58"
CLOSE_BG = "#3b3347"
BTN_FILL = "#3f2d63"
TAG_PURPLE = "#7c3fd9"
TEXT_FG = "#f6f1e7"
TEXT_DIM = "#a094ae"
GREEN = "#3fe05a"
GREEN_INK = "#0d3a1a"
AMBER = "#ffcf4a"
AMBER_INK = "#3d2c00"
ACCENT = "#ff2d7d"

# figure
INK = "#171717"
HALO = "#e9eef3"  # light outline so the silhouette reads on dark wallpapers
HEAD_FILL = "#f4f7f9"

# round control buttons
BTN_BG = "#1d2430"
BTN_HOVER = "#2a3446"
BTN_EDGE = "#393d49"
ICON = "#ffffff"

PANEL_L, PANEL_R, PANEL_BOTTOM = 60, 500, 300  # the panel grows upward from its bottom edge
PAD, TILE = 16, 52
SEND_W, CLOSE_W, ROW_H = 72, 34, 34
MAX_CHARS = 300
ECHO_CHARS = 44

PENDING = "…"  # shown while the assistant is thinking
LAUNCHING = "Launching Capri…"  # shown while the Capri launcher checks the app stayed up
PLACEHOLDER = "Say something…"

BTN_R, BTN_Y = 26, 602


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _chamfer(x1: float, y1: float, x2: float, y2: float, tl: float = 0, br: float = 0) -> list[float]:
    """Rectangle outline with the top-left and bottom-right corners cut off."""
    return [x1 + tl, y1, x2, y1, x2, y2 - br, x2 - br, y2, x1, y2, x1, y1 + tl]


class BabyAIDesktopWindow:
    def __init__(self, assistant: BabyAI, root: tk.Tk | None = None):
        self.assistant = assistant
        self.root = root or tk.Tk()
        self.root.title("BabyAI")
        self.root.resizable(False, False)

        self.transparent, canvas_bg = self._configure_window()
        self._place_window()

        self.canvas = tk.Canvas(
            self.root,
            width=WIDTH,
            height=HEIGHT,
            bg=canvas_bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._text = ""  # what the panel is saying
        self._echo = ""  # the user's last message, shown dim above it
        self._status = "READY"
        self._input_open = False
        self._placeholder_on = True
        self._busy = False
        self._capri_busy = False
        self._results: queue.Queue[str] = queue.Queue()
        self._notices: queue.Queue[str] = queue.Queue()
        self._drag: tuple[int, int] | None = None
        self._poll_id: str | None = None

        self._make_fonts()
        self._draw_figure()
        self._build_controls()
        self._bind_events()
        self._set_message(f"Hi, I'm {self._assistant_name()}. Pencil to type, mic to talk, chip to launch Capri.")
        self._poll_results()

        if not self.transparent:
            self.root.update()  # map the window first
            self._apply_panel_alpha()

    # ------------------------------------------------------------------ window

    def _configure_window(self) -> tuple[bool, str]:
        """Borderless + topmost, with the best transparency the platform offers.

        Returns (truly_transparent, canvas_background).
        """
        root = self.root
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self._windowing = root.tk.call("tk", "windowingsystem")
        if self._windowing == "win32":
            try:
                root.configure(bg=KEY_COLOR)
                root.attributes("-transparentcolor", KEY_COLOR)
                return True, KEY_COLOR
            except tk.TclError:
                pass
        elif self._windowing == "aqua":
            try:
                root.attributes("-transparent", True)
                root.configure(bg="systemTransparent")
                return True, "systemTransparent"
            except tk.TclError:
                pass

        # X11 / Wayland: no per-pixel transparency in Tk -> translucent dark panel.
        # (The alpha itself is applied once the window is mapped, see _apply_panel_alpha.)
        root.configure(bg=PANEL_BG)
        return False, PANEL_BG

    def _apply_panel_alpha(self) -> None:
        # X11 ignores -alpha when it is set before the window is mapped, so this
        # runs after the first update(). Needs a compositor to have any visible effect.
        try:
            self.root.attributes("-alpha", PANEL_ALPHA)
        except tk.TclError:
            pass

    def _place_window(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - WIDTH - 48)
        y = max(0, screen_h - HEIGHT - 72)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _make_fonts(self) -> None:
        families = {name.lower(): name for name in tkfont.families(self.root)}

        def pick(candidates: tuple[str, ...], fallback: str) -> str:
            for candidate in candidates:
                if candidate.lower() in families:
                    return families[candidate.lower()]
            return fallback

        sans = pick(
            ("Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans"),
            tkfont.nametofont("TkDefaultFont", root=self.root).actual("family"),
        )
        mono = pick(
            ("Share Tech Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Liberation Mono"),
            tkfont.nametofont("TkFixedFont", root=self.root).actual("family"),
        )
        self.font_body = tkfont.Font(root=self.root, family=sans, size=11)
        self.font_msg = tkfont.Font(root=self.root, family=sans, size=13, weight="bold")
        self.font_mono = tkfont.Font(root=self.root, family=mono, size=9)
        self.font_mono_b = tkfont.Font(root=self.root, family=mono, size=9, weight="bold")
        self.font_mono_s = tkfont.Font(root=self.root, family=mono, size=8)
        self.font_mono_sb = tkfont.Font(root=self.root, family=mono, size=8, weight="bold")

    def _assistant_name(self) -> str:
        try:
            return str(self.assistant.config.get("agent", {}).get("name", "BabyAI"))
        except Exception:
            return "BabyAI"

    def _model_name(self) -> str:
        try:
            return str(self.assistant.config.get("llm", {}).get("model", "")) or "local"
        except Exception:
            return "local"

    # ----------------------------------------------------------------- drawing

    def _draw_figure(self) -> None:
        """Floating stick-figure silhouette: halo, ink body, accent lines, head."""
        c = self.canvas
        cx = WIDTH // 2
        head_y, head_r = 344, 26
        neck_y = head_y + head_r
        shoulder_y = neck_y + 22
        hip_y = neck_y + 84
        foot_y = hip_y + 62
        limbs = [
            (cx, neck_y, cx, hip_y),  # torso
            (cx, shoulder_y, cx - 62, shoulder_y + 46),  # arms
            (cx, shoulder_y, cx + 62, shoulder_y + 46),
            (cx, hip_y, cx - 26, foot_y),  # legs
            (cx, hip_y, cx + 26, foot_y),
        ]

        # soft ground shadow so the figure reads as hovering
        c.create_oval(cx - 46, foot_y + 8, cx + 46, foot_y + 16, fill="#04070a", outline="", tags="figure")
        for x1, y1, x2, y2 in limbs:
            c.create_line(x1, y1, x2, y2, fill=HALO, width=14, capstyle="round", tags="figure")
        c.create_oval(
            cx - head_r - 4, head_y - head_r - 4, cx + head_r + 4, head_y + head_r + 4,
            fill=HALO, outline="", tags="figure",
        )
        for x1, y1, x2, y2 in limbs:
            c.create_line(x1, y1, x2, y2, fill=INK, width=8, capstyle="round", tags="figure")
        for x1, y1, x2, y2 in limbs:
            c.create_line(
                _lerp(x1, x2, 0.3), _lerp(y1, y2, 0.3), _lerp(x1, x2, 0.8), _lerp(y1, y2, 0.8),
                fill=ACCENT, width=3, capstyle="round", tags="figure",
            )
        c.create_oval(
            cx - head_r, head_y - head_r, cx + head_r, head_y + head_r,
            fill=HEAD_FILL, outline=INK, width=3, tags="figure",
        )

    def _build_controls(self) -> None:
        cx = WIDTH // 2
        self._make_button(cx - 90, BTN_Y, "pencil", self._toggle_input, self._draw_pencil)
        self._make_button(cx, BTN_Y, "mic", self._on_mic, self._draw_mic)
        self._make_button(cx + 90, BTN_Y, "capri", self._on_capri, self._draw_chip)

        self.entry = tk.Entry(
            self.root,
            bg=FIELD_BG,
            fg=TEXT_DIM,
            insertbackground=GREEN,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=self.font_body,
        )
        self.entry.insert(0, PLACEHOLDER)
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Escape>", self._on_entry_escape)
        self.entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.entry.bind("<FocusOut>", self._on_entry_focus_out)
        self._entry_win = self.canvas.create_window(0, 0, window=self.entry, state="hidden")

    def _make_button(self, x: int, y: int, name: str, command, draw_icon) -> None:
        c = self.canvas
        tag = f"btn_{name}"
        disc = c.create_oval(
            x - BTN_R, y - BTN_R, x + BTN_R, y + BTN_R,
            fill=BTN_BG, outline=BTN_EDGE, width=2, tags=("btn", tag),
        )
        draw_icon(x, y, ("btn", tag))

        def on_enter(_event):
            c.itemconfigure(disc, fill=BTN_HOVER)
            c.configure(cursor="hand2")

        def on_leave(_event):
            c.itemconfigure(disc, fill=BTN_BG)
            c.configure(cursor="")

        c.tag_bind(tag, "<Enter>", on_enter)
        c.tag_bind(tag, "<Leave>", on_leave)
        c.tag_bind(tag, "<Button-1>", lambda _event: command())

    def _draw_pencil(self, x: int, y: int, tags) -> None:
        c = self.canvas
        c.create_polygon(x - 10, y + 10, x - 9, y + 3, x - 3, y + 9, fill=ICON, outline="", tags=tags)
        c.create_line(x - 6, y + 6, x + 7, y - 7, width=7, fill=ICON, capstyle="butt", tags=tags)
        c.create_line(x + 8, y - 8, x + 11, y - 11, width=7, fill=ACCENT, capstyle="butt", tags=tags)

    def _draw_mic(self, x: int, y: int, tags) -> None:
        c = self.canvas
        c.create_polygon(
            x - 5, y - 8, x - 5, y - 8, x - 4, y - 13, x, y - 14, x + 4, y - 13, x + 5, y - 8,
            x + 5, y, x + 4, y + 3, x, y + 4, x - 4, y + 3, x - 5, y,
            smooth=True, fill=ICON, outline="", tags=tags,
        )
        c.create_arc(x - 10, y - 9, x + 10, y + 9, start=180, extent=180, style="arc", outline=ICON, width=2, tags=tags)
        c.create_line(x, y + 9, x, y + 14, fill=ICON, width=2, tags=tags)
        c.create_line(x - 5, y + 14, x + 5, y + 14, fill=ICON, width=2, capstyle="round", tags=tags)

    def _draw_chip(self, x: int, y: int, tags) -> None:
        c = self.canvas
        c.create_rectangle(x - 8, y - 8, x + 8, y + 8, outline=ICON, width=2, tags=tags)
        c.create_rectangle(x - 3, y - 3, x + 3, y + 3, fill=ACCENT, outline="", tags=tags)
        for d in (-4, 4):  # pins on all four sides
            c.create_line(x + d, y - 12, x + d, y - 8, fill=ICON, width=2, tags=tags)
            c.create_line(x + d, y + 8, x + d, y + 12, fill=ICON, width=2, tags=tags)
            c.create_line(x - 12, y + d, x - 8, y + d, fill=ICON, width=2, tags=tags)
            c.create_line(x + 8, y + d, x + 12, y + d, fill=ICON, width=2, tags=tags)

    # ------------------------------------------------------------- speech panel

    def _set_message(self, text: str, status: str = "READY") -> None:
        self._text = text
        self._status = status
        self._render_panel()

    def _render_panel(self) -> None:
        """Redraw the speech panel. Items are laid out from y=0, then the whole
        panel is moved so its bottom edge sits on PANEL_BOTTOM (it grows upward)."""
        c = self.canvas
        c.delete("panel")
        c.configure(cursor="")
        L, R = PANEL_L, PANEL_R
        top = 0
        text_x = L + PAD + TILE + 14
        msg_w = R - PAD - text_x

        # avatar tile: this assistant's own head, not a borrowed character
        tx, ty = L + PAD, top + PAD
        first = c.create_polygon(
            _chamfer(tx, ty, tx + TILE, ty + TILE, 8, 8), fill=TILE_FILL, outline=PANEL_EDGE, width=2, tags="panel"
        )
        hx, hy = tx + TILE // 2, ty + 19
        c.create_arc(  # shoulders
            hx - 17, hy + 13, hx + 17, hy + 47, start=0, extent=180, style="chord",
            fill=HALO, outline=ACCENT, width=2, tags="panel",
        )
        c.create_oval(hx - 11, hy - 11, hx + 11, hy + 11, fill=HEAD_FILL, outline=INK, width=2, tags="panel")

        # header: NAME / MODEL
        name = c.create_text(
            text_x, ty, anchor="nw", text=self._assistant_name().upper(), font=self.font_mono_b, fill=TEXT_FG, tags="panel"
        )
        c.create_text(
            c.bbox(name)[2] + 8, ty, anchor="nw", text="/  " + self._model_name().upper(),
            font=self.font_mono, fill=TEXT_DIM, tags="panel",
        )

        y = ty + 20
        if self._echo:
            echo = self._echo if len(self._echo) <= ECHO_CHARS else self._echo[: ECHO_CHARS - 1] + "…"
            line = c.create_text(text_x, y, anchor="nw", text="> " + echo, font=self.font_mono, fill=TEXT_DIM, tags="panel")
            y = c.bbox(line)[3] + 2
        label = self._text if len(self._text) <= MAX_CHARS else self._text[: MAX_CHARS - 1] + "…"
        message = c.create_text(
            text_x, y, anchor="nw", width=msg_w, text=label, font=self.font_msg, fill=TEXT_FG, tags="panel"
        )
        bottom = max(c.bbox(message)[3], ty + TILE) + 12

        if self._input_open:
            fx1 = L + PAD
            fx2 = R - PAD - SEND_W - CLOSE_W - 16
            c.create_rectangle(fx1, bottom, fx2, bottom + ROW_H, fill=FIELD_BG, outline="", tags="panel")
            c.create_rectangle(fx1, bottom + ROW_H - 3, fx2, bottom + ROW_H, fill=GREEN, outline="", tags="panel")
            c.coords(self._entry_win, (fx1 + fx2) / 2, bottom + (ROW_H - 3) / 2)
            c.itemconfigure(self._entry_win, width=fx2 - fx1 - 12, height=ROW_H - 8, state="normal")
            sx1 = fx2 + 8
            self._panel_button(
                "send", _chamfer(sx1, bottom, sx1 + SEND_W, bottom + ROW_H, 6, 6), GREEN, GREEN, "SEND", GREEN_INK,
                (sx1 + SEND_W / 2, bottom + ROW_H / 2), self.font_mono_b,
            )
            cx1 = sx1 + SEND_W + 8
            self._panel_button(
                "close", _chamfer(cx1, bottom, cx1 + CLOSE_W, bottom + ROW_H, 0, 0), CLOSE_BG, CLOSE_BG, "", TEXT_FG,
                (cx1 + CLOSE_W / 2, bottom + ROW_H / 2), self.font_mono_b,
            )
            mx, my = cx1 + CLOSE_W / 2, bottom + ROW_H / 2  # drawn, not a font glyph, so it never changes size
            for dx in (-1, 1):
                c.create_line(mx - 5, my - 5 * dx, mx + 5, my + 5 * dx, fill=TEXT_FG, width=2, tags=("panel", "btn", "act_close"))
            note_y = bottom + ROW_H + 10
            c.create_rectangle(fx1, note_y, fx1 + 22, note_y + 14, fill=TAG_PURPLE, outline="", tags="panel")
            c.create_text(fx1 + 11, note_y + 7, text="AI", font=self.font_mono_sb, fill="#ffffff", tags="panel")
            note = c.create_text(
                fx1 + 30, note_y + 7, anchor="w", text="Enter to send  ·  Esc to close", font=self.font_mono_s,
                fill=TEXT_DIM, tags="panel",
            )
            bottom = c.bbox(note)[3]
        else:
            c.itemconfigure(self._entry_win, state="hidden")
            self._panel_button(
                "talk", _chamfer(text_x, bottom - 2, text_x + 96, bottom + 20, 5, 5), BTN_FILL, PANEL_EDGE, "TALK TO ME",
                TEXT_FG, (text_x + 48, bottom + 9), self.font_mono_sb,
            )
            bottom += 20

        height = bottom + PAD
        edge = c.create_polygon(
            _chamfer(L, top, R, height, 12, 22), fill=PANEL_FILL, outline=PANEL_EDGE, width=2, tags="panel"
        )
        c.create_line(R, top + 4, R, height - 22, fill=PANEL_EDGE, width=5, tags="panel")
        c.tag_lower(edge, first)

        # status tag straddling the top-right edge
        busy = self._status != "READY"
        tag_text = f"AI · {self._status}"
        tag_w = 14 + self.font_mono_sb.measure(tag_text)
        gx2 = R - PAD
        c.create_polygon(
            _chamfer(gx2 - tag_w, top - 9, gx2, top + 13, 5, 5), fill=AMBER if busy else GREEN, outline="", tags="panel"
        )
        c.create_text(
            gx2 - tag_w / 2, top + 2, text=tag_text, font=self.font_mono_sb, fill=AMBER_INK if busy else GREEN_INK, tags="panel"
        )

        dy = PANEL_BOTTOM - height
        c.move("panel", 0, dy)
        if self._input_open:
            c.move(self._entry_win, 0, dy)

    def _panel_button(self, name, points, fill, outline, label, label_fg, center, font) -> None:
        tags = ("panel", "btn", f"act_{name}")
        self.canvas.create_polygon(points, fill=fill, outline=outline, width=2, tags=tags)
        self.canvas.create_text(center[0], center[1], text=label, font=font, fill=label_fg, tags=tags)

    # ------------------------------------------------------------- interaction

    def _toggle_input(self) -> None:
        if self._input_open:
            self._close_input()
        else:
            self._open_input()

    def _open_input(self) -> None:
        self._input_open = True
        self._render_panel()
        self.entry.focus_force()

    def _close_input(self) -> None:
        self._input_open = False
        self._render_panel()
        self.root.focus_set()

    def _entry_text(self) -> str:
        return "" if self._placeholder_on else self.entry.get().strip()

    def _on_entry_focus_in(self, _event) -> None:
        if self._placeholder_on:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT_FG)
            self._placeholder_on = False

    def _on_entry_focus_out(self, _event) -> None:
        if not self.entry.get():
            self.entry.insert(0, PLACEHOLDER)
            self.entry.configure(fg=TEXT_DIM)
            self._placeholder_on = True

    def _on_entry_return(self, _event) -> str:
        self._on_send()
        return "break"

    def _on_entry_escape(self, _event) -> str:
        self._close_input()
        return "break"

    def _on_send(self) -> None:
        text = self._entry_text()
        if text and not self._busy:
            self.entry.delete(0, "end")
            self._submit(text)

    def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._echo = text
        self._set_message(PENDING, "THINKING")
        threading.Thread(target=self._ask_assistant, args=(text,), daemon=True).start()

    def _ask_assistant(self, text: str) -> None:
        # Runs off the Tk thread; the result is handed back through a queue
        # because Tk widgets must only be touched from the main thread.
        try:
            reply = self.assistant.process_user_input(text)
        except Exception as exc:
            reply = f"Something went wrong: {exc}"
        self._results.put(reply or "(no reply)")

    def _poll_results(self) -> None:
        try:
            while True:
                self._set_message(self._results.get_nowait())
                self._busy = False
        except queue.Empty:
            pass
        try:
            while True:
                self._set_message(self._notices.get_nowait())
                self._capri_busy = False
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self._poll_results)

    def _on_mic(self) -> None:
        if self._busy:
            return
        try:
            from babyai.voice.stt import SpeechToText

            stt = SpeechToText()
            available = stt.is_available()
            heard = stt.transcribe()
        except Exception as exc:
            available, heard = False, f"Voice input failed: {exc}"
        if available and heard:
            self._submit(heard)
        else:
            self._set_message(heard or "Voice input isn't available.")

    def _on_capri(self) -> None:
        if self._capri_busy:
            return
        self._capri_busy = True
        self._set_message(LAUNCHING, "LAUNCHING")
        threading.Thread(target=self._launch_capri, daemon=True).start()

    def _launch_capri(self) -> None:
        # launch_capri() waits ~1.5 s to see whether Capri stays up, so it runs off
        # the Tk thread; the outcome comes back through a queue like assistant replies.
        try:
            from babyai.capri_launcher import launch_capri

            message = launch_capri().message
        except Exception as exc:
            message = f"Could not launch Capri: {exc}"
        self._notices.put(message)

    # ------------------------------------------------------------ drag / close

    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<ButtonPress-1>", self._start_drag)
        c.bind("<B1-Motion>", self._move_window)

        for name, handler in (("send", self._on_send), ("close", self._close_input), ("talk", self._open_input)):
            tag = f"act_{name}"
            c.tag_bind(tag, "<Button-1>", lambda _event, run=handler: run())
            c.tag_bind(tag, "<Enter>", lambda _event: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda _event: c.configure(cursor=""))

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Quit BabyAI", command=self.close)
        c.bind("<Button-2>" if self._windowing == "aqua" else "<Button-3>", self._popup_menu)

        self.root.bind("<Escape>", self.close)
        self.root.bind("<Control-q>", self.close)

    def _start_drag(self, event) -> None:
        current = self.canvas.find_withtag("current")
        if current and "btn" in self.canvas.gettags(current[0]):
            self._drag = None  # a button press, not a window drag
            return
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _move_window(self, event) -> None:
        if self._drag is None:
            return
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _popup_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def close(self, _event=None) -> None:
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch_desktop_ui(assistant: BabyAI) -> None:
    BabyAIDesktopWindow(assistant).run()


if __name__ == "__main__":
    from babyai.agent.core import BabyAI

    launch_desktop_ui(BabyAI())
