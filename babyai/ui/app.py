"""Floating desktop overlay for BabyAI (Tkinter).

A borderless, always-on-top assistant silhouette with a terminal-style speech
bubble and a small control pill:

* the bubble is a transcript: a dim ``> you:`` line, then the assistant's reply
  in monospace that types itself out; your own typing appears inline on the
  ``> you:`` line, so there is no separate input box;
* pencil = type a message, wave = voice input, chip = launch the separate
  Capri app (see babyai.capri_launcher).

Transparency depends on the platform / compositor:

* Windows and macOS: the window background is keyed out, so only the figure,
  bubble and buttons are visible - there is no app frame at all.
* X11 / Wayland (via XWayland), i.e. most Linux desktops: Tk cannot do per-pixel
  transparency, so the overlay uses a semi-transparent dark panel instead. The
  alpha needs a running compositor; without one the panel is simply opaque dark,
  never a white or grey box.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import TYPE_CHECKING

from babyai.ui.behavior import SleepClock, eye_offset, thinking_label, timings_from_config

if TYPE_CHECKING:  # the UI only relies on the assistant's public methods at runtime
    from babyai.agent.core import BabyAI

WIDTH, HEIGHT = 560, 640

KEY_COLOR = "#010203"  # keyed out to transparent on Windows (not used elsewhere)
PANEL_BG = "#080d13"  # window fallback when real transparency is unavailable
PANEL_ALPHA = 0.88

# speech bubble
BUBBLE_FILL = "#0b0e14"
CYAN = "#3ad6eb"
RED = "#e82440"
NEON = "#38b0ff"  # block cursor
REPLY_FG = "#8bd0f0"
DIM = "#8a93a3"
ACCENT = "#ff2d7d"

# figure
INK = "#171717"
HALO = "#e9eef3"  # light outline so the silhouette reads on dark wallpapers
HEAD_FILL = "#f4f7f9"
HEAD_Y, HEAD_R = 344, 26  # head centre (canvas y) and radius
EYE_DX, EYE_DY, EYE_R = 9, -3, 3.5  # eye spacing from the centre line, height, dot size

# control pill
PILL_FILL = "#141519"
PILL_EDGE = "#464952"
PILL_HOVER = "#2a2d36"
ICON = "#ffffff"

BUBBLE_L, BUBBLE_R, BUBBLE_BOTTOM = 60, 500, 290  # the bubble grows upward from its bottom edge
TAIL = 16
MAX_LINES = 8
MAX_CHARS = 420
MAX_DRAFT = 500

TICK_MS = 40
TYPE_SPEED = 2  # characters revealed per tick (~50 per second)
BLINK_TICKS = 12  # cursor blink period / 2
POINTER_TICKS = 5  # sample the mouse pointer every 5th tick (~200 ms)
Z_TICKS = 12  # the sleeping "z z z" gains one letter every 12 ticks (~0.5 s)
Z_CYCLE = (1, 2, 3, 3, 3)  # letters shown over one cycle; it rests on the full "z z z"

LAUNCHING = "Launching Capri…"  # shown while the Capri launcher checks the app stayed up
BTN_Y = 602


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _plate(x1: float, y1: float, x2: float, y2: float, cut: float, tip_x: float, tail: float) -> list[float]:
    """Bubble outline: left corners cut, pointer tail on the bottom edge toward tip_x."""
    near = max(x1 + cut + 6, tip_x - tail * 0.8)
    far = min(x2 - 6, tip_x + tail * 0.2)
    return [
        x1 + cut, y1, x2, y1, x2, y2, far, y2, tip_x, y2 + tail, near, y2,
        x1 + cut, y2, x1, y2 - cut, x1, y1 + cut,
    ]


def _wrap(text: str, first_cols: int, cols: int) -> list[str]:
    """Greedy word wrap; the first line may be narrower (it follows the name label)."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        width = first_cols if not lines else cols
        for word in paragraph.split(" "):
            while len(word) > width:  # hard-split words longer than a line
                if line:
                    lines.append(line)
                    line = ""
                    width = cols
                lines.append(word[:width])
                word = word[width:]
                width = cols
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
                width = cols
        lines.append(line)
    return lines


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
            takefocus=1,
        )
        self.canvas.pack(fill="both", expand=True)

        self._reply = ""  # what the assistant is saying (revealed progressively)
        self._echo = ""  # the user's last sent message
        self._draft = ""  # what the user is typing right now
        self._shown = 0  # reply characters revealed so far
        self._target = 0  # reveal count at which the whole reply is visible
        self._thinking = False
        self._think_since: float | None = None  # monotonic time the current wait began
        self._think_text: str | None = None  # "THINKING… N s" once the wait gets long
        self._input_open = False
        self._tick_n = 0
        self._busy = False
        self._capri_busy = False
        self._results: queue.Queue[str] = queue.Queue()
        self._notices: queue.Queue[str] = queue.Queue()
        self._drag: tuple[int, int] | None = None
        self._poll_id: str | None = None
        self._tick_id: str | None = None
        self._timings = timings_from_config(getattr(assistant, "config", None))
        self._clock = SleepClock(time.monotonic(), self._timings.sleep_after_s, self._timings.wake_radius_px)
        self._last_pointer: tuple[int, int] | None = None
        self._look = (0, 0)  # how far the eyes are slid toward the pointer, in pixels
        self._face_drawn: tuple | None = None  # what the eyes and "z z z" currently show

        self._make_fonts()
        self._draw_figure()
        self._build_controls()
        self._bind_events()
        self._set_reply(f"Hi, I'm {self._assistant_name()}. Pencil to type, wave to talk, chip to launch Capri.")
        self._poll_results()
        self._tick()

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
        mono = tkfont.nametofont("TkFixedFont", root=self.root).actual("family")
        for candidate in ("Share Tech Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Liberation Mono"):
            if candidate.lower() in families:
                mono = families[candidate.lower()]
                break
        self.font_mono = tkfont.Font(root=self.root, family=mono, size=10)
        self.font_label = tkfont.Font(root=self.root, family=mono, size=10, weight="bold")
        self.font_echo = tkfont.Font(root=self.root, family=mono, size=10, slant="italic")
        self.char_w = max(1, self.font_mono.measure("M"))
        self.line_h = self.font_mono.metrics("linespace") + 5

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
        head_y, head_r = HEAD_Y, HEAD_R
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
        self._sync_face()

    # -------------------------------------------------------- eyes, sleep, wake

    def _face_state(self) -> tuple:
        asleep = self._clock.asleep
        letters = Z_CYCLE[(self._tick_n // Z_TICKS) % len(Z_CYCLE)] if asleep else 0
        look_x, look_y = (0, 0) if asleep else self._look
        return (asleep, letters, look_x, look_y)

    def _sync_face(self) -> None:
        """Redraw the eyes and the sleeping "z z z" only when what they show has changed."""
        state = self._face_state()
        if state != self._face_drawn:
            self._face_drawn = state
            self._draw_face(*state)

    def _draw_face(self, asleep: bool, letters: int, look_x: int, look_y: int) -> None:
        c = self.canvas
        c.delete("face")
        cx = WIDTH // 2
        for side in (-1, 1):
            ex, ey = cx + side * EYE_DX + look_x, HEAD_Y + EYE_DY + look_y
            if asleep:  # closed eyes
                c.create_line(ex - 5, ey, ex + 5, ey, fill=INK, width=3, capstyle="round", tags="face")
            else:
                c.create_oval(ex - EYE_R, ey - EYE_R, ex + EYE_R, ey + EYE_R, fill=INK, outline="", tags="face")
        for i in range(letters):  # "z", "z z", "z z z" drifting up and out to the right of the head
            c.create_text(cx + 38 + i * 14, HEAD_Y - 14 - i * 12, text="z", font=self.font_label, fill=CYAN, tags="face")

    def _wake(self) -> None:
        """A click, key press or reply counts as activity: wake up and restart the idle timer."""
        self._clock.touch(time.monotonic())
        self._sync_face()

    def _presence_tick(self) -> None:
        """Fall asleep after a long idle spell; wake when the pointer moves close to the head."""
        now = time.monotonic()
        if (self._busy or self._capri_busy) and not self._clock.manual:  # waiting on a reply is not idleness
            self._clock.touch(now)
        distance, moved = None, False
        if self._tick_n % POINTER_TICKS == 0:
            self._look = (0, 0)
            try:
                px, py = self.root.winfo_pointerxy()
            except tk.TclError:
                px = py = None
            if px is not None:
                head_x = self.root.winfo_rootx() + WIDTH // 2
                head_y = self.root.winfo_rooty() + HEAD_Y
                distance = math.hypot(px - head_x, py - head_y)
                self._look = eye_offset(px - head_x, py - head_y, radius=self._timings.look_radius_px)
                moved = self._last_pointer is not None and (px, py) != self._last_pointer
                self._last_pointer = (px, py)
        self._clock.tick(now, distance, moved)
        self._sync_face()

    def _build_controls(self) -> None:
        c = self.canvas
        cx = WIDTH // 2
        x1, x2 = cx - 73, cx + 27
        self._rounded_rect(x1, BTN_Y - 18, x2, BTN_Y + 18, 18, fill=PILL_FILL, outline=PILL_EDGE, width=2, tags="pill")
        c.create_line((x1 + x2) / 2, BTN_Y - 9, (x1 + x2) / 2, BTN_Y + 9, fill=PILL_EDGE, width=1, tags="pill")
        self._make_button(x1 + 25, BTN_Y, "pencil", self._toggle_input, self._draw_pencil, r=15)
        self._make_button(x2 - 25, BTN_Y, "voice", self._on_mic, self._draw_wave, r=15)
        self._make_button(cx + 55, BTN_Y, "capri", self._on_capri, self._draw_chip, r=18, ring=True)

    def _make_button(self, x: float, y: float, name: str, command, draw_icon, r: int, ring: bool = False) -> None:
        c = self.canvas
        tag = f"btn_{name}"
        disc = c.create_oval(
            x - r, y - r, x + r, y + r,
            fill=PILL_FILL, outline=PILL_EDGE if ring else "", width=2, tags=("btn", tag),
        )
        draw_icon(x, y, ("btn", tag))

        def on_enter(_event):
            c.itemconfigure(disc, fill=PILL_HOVER)
            c.configure(cursor="hand2")

        def on_leave(_event):
            c.itemconfigure(disc, fill=PILL_FILL)
            c.configure(cursor="")

        c.tag_bind(tag, "<Enter>", on_enter)
        c.tag_bind(tag, "<Leave>", on_leave)
        c.tag_bind(tag, "<Button-1>", lambda _event: command())

    def _draw_pencil(self, x: float, y: float, tags) -> None:
        c = self.canvas
        c.create_polygon(x - 10, y + 10, x - 9, y + 3, x - 3, y + 9, fill=ICON, outline="", tags=tags)
        c.create_line(x - 6, y + 6, x + 7, y - 7, width=7, fill=ICON, capstyle="butt", tags=tags)
        c.create_line(x + 8, y - 8, x + 11, y - 11, width=7, fill=ACCENT, capstyle="butt", tags=tags)

    def _draw_wave(self, x: float, y: float, tags) -> None:
        for dx, half in ((-8, 3), (-4, 7), (0, 11), (4, 7), (8, 3)):
            self.canvas.create_line(x + dx, y - half, x + dx, y + half, fill=CYAN, width=2, capstyle="round", tags=tags)

    def _draw_chip(self, x: float, y: float, tags) -> None:
        c = self.canvas
        c.create_rectangle(x - 8, y - 8, x + 8, y + 8, outline=ICON, width=2, tags=tags)
        c.create_rectangle(x - 3, y - 3, x + 3, y + 3, fill=ACCENT, outline="", tags=tags)
        for d in (-4, 4):  # pins on all four sides
            c.create_line(x + d, y - 12, x + d, y - 8, fill=ICON, width=2, tags=tags)
            c.create_line(x + d, y + 8, x + d, y + 12, fill=ICON, width=2, tags=tags)
            c.create_line(x - 12, y + d, x - 8, y + d, fill=ICON, width=2, tags=tags)
            c.create_line(x + 8, y + d, x + 12, y + d, fill=ICON, width=2, tags=tags)

    # ------------------------------------------------------------ speech bubble

    def _set_reply(self, text: str, thinking: bool = False) -> None:
        self._reply = text
        self._thinking = thinking
        self._think_since = time.monotonic() if thinking else None
        self._think_text = None
        self._shown = 0  # the reply types itself out from the start
        self._render_bubble()

    def _render_bubble(self) -> None:
        """Redraw the transcript bubble. It is laid out from y=0, then moved so its
        bottom edge sits on BUBBLE_BOTTOM (it grows upward, tail pointing at the figure)."""
        c = self.canvas
        c.delete("bubble")
        L, R = BUBBLE_L, BUBBLE_R
        x0 = L + 26
        cw, lh = self.char_w, self.line_h
        cols = max(12, int((R - 16 - x0) // cw))
        label = f"{self._assistant_name().upper()}:"
        label_px = self.font_label.measure(label) + cw  # the reply starts one space after the label
        label_cols = -(-label_px // cw)  # ceil

        reply = self._reply if len(self._reply) <= MAX_CHARS else self._reply[: MAX_CHARS - 1] + "…"
        lines = _wrap(reply, cols - label_cols, cols) if reply else [""]
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES]
            lines[-1] = lines[-1][: cols - 2].rstrip() + "…"
        self._target = sum(len(line) + 1 for line in lines)

        has_echo = self._input_open or bool(self._echo)
        pad = 12
        height = pad * 2 + (len(lines) + (1 if has_echo else 0)) * lh
        c.create_polygon(
            _plate(L, 0, R, height, 10, WIDTH // 2, TAIL),
            fill=BUBBLE_FILL, outline=CYAN, width=2, joinstyle="miter", tags="bubble",
        )
        c.create_line(L + 11, 10, L + 11, height - 10, fill=RED, width=3, tags="bubble")

        blink_on = (self._tick_n // BLINK_TICKS) % 2 == 0
        y = pad
        if has_echo:
            if self._input_open:
                room = cols - 9
                body = self._draft if len(self._draft) <= room else "…" + self._draft[-room:]
            else:
                room = cols - 7
                body = self._echo if len(self._echo) <= room else self._echo[: room - 1] + "…"
            text = "> you: " + body
            c.create_text(x0, y, anchor="nw", text=text, font=self.font_echo, fill=DIM, tags="bubble")
            if self._input_open and blink_on:
                self._block(x0 + self.font_echo.measure(text), y)
            y += lh

        c.create_text(x0, y, anchor="nw", text=label, font=self.font_label, fill=RED, tags="bubble")
        if self._thinking:
            tx = x0 + label_px
            if self._think_text:
                c.create_text(tx, y, anchor="nw", text=self._think_text, font=self.font_mono, fill=DIM, tags="bubble")
                tx += self.font_mono.measure(self._think_text) + cw
            if blink_on:
                self._block(tx, y)
        else:
            remaining = self._shown
            for i, line in enumerate(lines):
                lx = x0 + (label_px if i == 0 else 0)
                ly = y + i * lh
                part = line[: max(0, remaining)]
                if part:
                    c.create_text(lx, ly, anchor="nw", text=part, font=self.font_mono, fill=REPLY_FG, tags="bubble")
                if remaining < len(line) + 1:  # the reveal is still in this line
                    if self._shown < self._target:
                        self._block(lx + self.font_mono.measure(part), ly)
                    break
                remaining -= len(line) + 1

        c.move("bubble", 0, BUBBLE_BOTTOM - height)

    def _block(self, x: float, y: float) -> None:
        self.canvas.create_rectangle(
            x, y + 2, x + self.char_w * 0.8, y + self.line_h - 5, fill=NEON, outline="", tags="bubble"
        )

    def _tick(self) -> None:
        self._tick_n += 1
        dirty = False
        if not self._thinking and self._shown < self._target:
            self._shown = min(self._target, self._shown + TYPE_SPEED)
            dirty = True
        if self._thinking and self._think_since is not None:
            text = thinking_label(time.monotonic() - self._think_since, self._timings.think_after_s)
            if text != self._think_text:
                self._think_text = text
                dirty = True
        if self._tick_n % BLINK_TICKS == 0 and (self._input_open or self._thinking):
            dirty = True
        self._presence_tick()
        if dirty:
            self._render_bubble()
        self._tick_id = self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------- interaction

    def _toggle_input(self) -> None:
        if self._input_open:
            self._close_input()
        else:
            self._open_input()

    def _open_input(self) -> None:
        self._input_open = True
        self._draft = ""
        self._render_bubble()
        self.canvas.focus_force()

    def _close_input(self) -> None:
        self._input_open = False
        self._draft = ""
        self._render_bubble()
        self.root.focus_set()

    def _on_key(self, event) -> str | None:
        self._wake()
        if not self._input_open:
            return None
        if event.keysym in ("Return", "KP_Enter"):
            self._send()
        elif event.keysym == "Escape":
            self._close_input()
        elif event.keysym == "BackSpace":
            self._draft = self._draft[:-1]
        elif (event.state & 0x4) and event.keysym.lower() == "v":  # Ctrl+V
            try:
                pasted = " ".join(self.root.clipboard_get().split())
            except tk.TclError:
                pasted = ""
            self._draft = (self._draft + pasted)[:MAX_DRAFT]
        elif event.char and event.char.isprintable() and len(self._draft) < MAX_DRAFT:
            self._draft += event.char
        else:
            return None
        self._render_bubble()
        return "break"

    def _send(self) -> None:
        text = self._draft.strip()
        if not text or self._busy:
            return
        self._input_open = False
        self._draft = ""
        self._submit(text)

    def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._echo = text
        self._set_reply("", thinking=True)
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
                self._set_reply(self._results.get_nowait())
                self._busy = False
                self._wake()
        except queue.Empty:
            pass
        try:
            while True:
                self._set_reply(self._notices.get_nowait())
                self._capri_busy = False
                self._wake()
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
            self._set_reply(heard or "Voice input isn't available.")

    def _on_capri(self) -> None:
        if self._capri_busy:
            return
        self._capri_busy = True
        self._set_reply(LAUNCHING)
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
        c.bind("<Key>", self._on_key)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Sleep", command=self._toggle_sleep)  # reads "Wake" while asleep
        self.menu.add_command(label="Quit BabyAI", command=self.close)
        c.bind("<Button-2>" if self._windowing == "aqua" else "<Button-3>", self._popup_menu)

        self.root.bind("<Key>", lambda _event: self._wake(), add="+")  # keys that land on the window, not the canvas
        self.root.bind("<Escape>", self.close)  # Esc while typing is handled (and stopped) in _on_key
        self.root.bind("<Control-q>", self.close)

    def _start_drag(self, event) -> None:
        self._wake()
        current = self.canvas.find_withtag("current")
        if current and "btn" in self.canvas.gettags(current[0]):
            self._drag = None  # a button press, not a window drag
            return
        if self._input_open:
            self.canvas.focus_force()
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _move_window(self, event) -> None:
        if self._drag is None:
            return
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _toggle_sleep(self) -> None:
        if self._clock.asleep:
            self._wake()
        else:
            self._clock.sleep()
            self._sync_face()

    def _popup_menu(self, event) -> None:
        self.menu.entryconfigure(0, label="Wake" if self._clock.asleep else "Sleep")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def close(self, _event=None) -> None:
        for after_id in (self._poll_id, self._tick_id):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
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
