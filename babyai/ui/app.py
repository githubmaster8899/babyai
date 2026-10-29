"""Floating desktop overlay for BabyAI (Tkinter).

A borderless, always-on-top assistant silhouette with dark chat bubbles and a
minimal control row (pencil = type a message, mic = voice input).

Transparency depends on the platform / compositor:

* Windows and macOS: the window background is keyed out, so only the figure,
  bubbles and buttons are visible - there is no app frame at all.
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
PANEL_BG = "#080d13"  # fallback panel when real transparency is unavailable
PANEL_ALPHA = 0.88

BUBBLE_BG = "#0f1720"
BUBBLE_EDGE = "#2a3a4d"
TEXT_FG = "#f0f2f5"
ACCENT = "#ff2d7d"

INK = "#171717"
HALO = "#e9eef3"  # light outline so the silhouette reads on dark wallpapers
HEAD_FILL = "#f4f7f9"

BTN_BG = "#1d2430"
BTN_HOVER = "#2a3446"
BTN_EDGE = "#393d49"
ICON = "#ffffff"

BUBBLE_TOP, BUBBLE_BOTTOM, BUBBLE_GAP = 16, 304, 10
BUBBLE_MAX_W = 400
MAX_BUBBLES = 4
MAX_CHARS = 480
PENDING = "…"  # shown while the assistant is thinking

BTN_R, BTN_Y = 26, 602
ENTRY_Y = 548


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


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

        self.messages: list[tuple[str, str]] = []
        self._busy = False
        self._results: queue.Queue[str] = queue.Queue()
        self._drag: tuple[int, int] | None = None
        self._poll_id: str | None = None

        self._make_fonts()
        self._draw_figure()
        self._build_controls()
        self._bind_events()
        self._add_message("assistant", f"Hi, I'm {self._assistant_name()}. Tap the pencil to type, or the mic to talk.")
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
        family = tkfont.nametofont("TkDefaultFont", root=self.root).actual("family")
        for candidate in ("Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans"):
            if candidate.lower() in families:
                family = families[candidate.lower()]
                break
        self.font_body = tkfont.Font(root=self.root, family=family, size=12)

    def _assistant_name(self) -> str:
        try:
            return str(self.assistant.config.get("agent", {}).get("name", "BabyAI"))
        except Exception:
            return "BabyAI"

    # ----------------------------------------------------------------- drawing

    def _rounded_rect(self, x1: float, y1: float, x2: float, y2: float, r: float, **kw) -> int:
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kw)

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
        self._make_button(cx - 50, BTN_Y, "pencil", self._toggle_entry, self._draw_pencil)
        self._make_button(cx + 50, BTN_Y, "mic", self._on_mic, self._draw_mic)

        self.entry = tk.Entry(
            self.root,
            bg=BUBBLE_BG,
            fg=TEXT_FG,
            insertbackground=TEXT_FG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BUBBLE_EDGE,
            highlightcolor=ACCENT,
            font=self.font_body,
        )
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Escape>", self._on_entry_escape)
        self._entry_win = self.canvas.create_window(
            WIDTH // 2, ENTRY_Y, window=self.entry, width=380, height=32, state="hidden"
        )

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
        self._rounded_rect(x - 5, y - 13, x + 5, y + 3, 5, fill=ICON, outline="", tags=tags)
        c.create_arc(x - 10, y - 9, x + 10, y + 9, start=180, extent=180, style="arc", outline=ICON, width=2, tags=tags)
        c.create_line(x, y + 9, x, y + 14, fill=ICON, width=2, tags=tags)
        c.create_line(x - 5, y + 14, x + 5, y + 14, fill=ICON, width=2, capstyle="round", tags=tags)

    # ----------------------------------------------------------------- bubbles

    def _add_message(self, role: str, text: str) -> None:
        self.messages.append((role, text))
        del self.messages[:-MAX_BUBBLES]
        self._redraw_bubbles()

    def _replace_pending(self, text: str) -> None:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index] == ("assistant", PENDING):
                self.messages[index] = ("assistant", text)
                break
        self._redraw_bubbles()

    def _redraw_bubbles(self) -> None:
        self.canvas.delete("bubble")
        bottom = BUBBLE_BOTTOM
        for role, text in reversed(self.messages):
            top = self._draw_bubble(role, text, bottom, force=bottom == BUBBLE_BOTTOM)
            if top is None:
                break
            bottom = top - BUBBLE_GAP

    def _draw_bubble(self, role: str, text: str, bottom: int, force: bool) -> int | None:
        c = self.canvas
        pad_x, pad_y, margin = 16, 11, 24
        label = text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"
        item = c.create_text(
            0, 0, text=label, anchor="nw", width=BUBBLE_MAX_W - 2 * pad_x,
            font=self.font_body, fill=TEXT_FG, tags="bubble",
        )
        x1, y1, x2, y2 = c.bbox(item)
        width = (x2 - x1) + 2 * pad_x
        height = (y2 - y1) + 2 * pad_y
        top = bottom - height
        if top < BUBBLE_TOP and not force:
            c.delete(item)
            return None
        left = margin if role == "assistant" else WIDTH - margin - width
        c.coords(item, left + pad_x, top + pad_y)
        shape = self._rounded_rect(
            left, top, left + width, top + height, 18,
            fill=BUBBLE_BG, outline=ACCENT if role == "user" else BUBBLE_EDGE, width=2, tags="bubble",
        )
        c.tag_lower(shape, item)
        return top

    # ------------------------------------------------------------- interaction

    def _toggle_entry(self) -> None:
        if self.canvas.itemcget(self._entry_win, "state") == "hidden":
            self.canvas.itemconfigure(self._entry_win, state="normal")
            self.entry.focus_force()
        else:
            self._hide_entry()

    def _hide_entry(self) -> None:
        self.canvas.itemconfigure(self._entry_win, state="hidden")
        self.root.focus_set()

    def _on_entry_return(self, _event) -> str:
        text = self.entry.get().strip()
        if text and not self._busy:
            self.entry.delete(0, "end")
            self._submit(text)
        return "break"

    def _on_entry_escape(self, _event) -> str:
        self._hide_entry()
        return "break"

    def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._add_message("user", text)
        self._add_message("assistant", PENDING)
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
                self._replace_pending(self._results.get_nowait())
                self._busy = False
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
            self._add_message("assistant", heard or "Voice input isn't available.")

    # ------------------------------------------------------------ drag / close

    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<ButtonPress-1>", self._start_drag)
        c.bind("<B1-Motion>", self._move_window)

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
