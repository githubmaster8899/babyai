"""Floating desktop overlay for BabyAI (Tkinter) - Capri's body with BabyAI's brain.

A borderless, always-on-top companion with a terminal-style speech bubble and a
small control pill. It is one Python app: the character is Capri's own sprites and
animation rules (babyai.ui.capri_body / babyai.ui.sprites, from capri/assets), and
everything she says and does goes through BabyAI (LLM, memory, tools, safety gate).
No separate Capri process is involved.

* the bubble is a transcript: a dim ``> you:`` line, then her reply in monospace
  that types itself out at Capri's 28 letters a second while her mouth follows the
  letters; your own typing appears inline on the ``> you:`` line. Where a borderless
  window can't take keyboard focus (Wayland desktops, via XWayland) a small "Talk to ..."
  box catches the keys instead and the bubble mirrors them (``[ui] typing``, see
  babyai.ui.typing_box);
* pencil = type a message, wave = voice input; stroke the pointer back and forth
  over her to pet her; right-click for Sleep / Wake / Quit;
* after a long quiet spell she slips behind the screen edge, peeks back now and
  then, and comes out on the next click, key or reply (``[ui] hide_and_seek``, see
  babyai.ui.behavior). Dragging her sets a new resting spot;
* when a tool call needs your say-so (the safety gate), the bubble asks and you
  answer Y / N or click.
* without the sprite files (or with ``[ui] character = "stick"``) a stick-figure
  silhouette is drawn instead.

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
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import TYPE_CHECKING

from babyai.ui.behavior import (
    BubbleTimer,
    HideAndSeek,
    InputHistory,
    SleepClock,
    cursor_line,
    eye_offset,
    hide_timings_from_config,
    needs_typing_box,
    scroll_top,
    thinking_label,
    timings_from_config,
)
from babyai.ui.capri_body import CLIPS, Animator, PetDetector, letters_revealed, mouth_cell, pet_line
from babyai.ui.persona import apply_character
from babyai.ui.sounds import SoundPlayer
from babyai.ui.summon import SummonServer, socket_path
from babyai.ui.sprites import DEFAULT_SCALE, SpriteBank
from babyai.ui.typing_box import TypingBox
from babyai.ui.xshape import XShape

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

BUBBLE_L, BUBBLE_R, BUBBLE_BOTTOM = 60, 500, 290  # the bubble grows upward from its bottom edge (stick figure)
SPRITE_BOTTOM = 576  # canvas y of the bottom edge of her sprite frame
SPRITE_HAIR = 0.10  # fraction of the frame above the top of her hair
# Her window is clipped to one outline per pose family, not per frame: re-cutting the shape on every animation
# frame made her flicker (newly exposed bits show the dark background until Tk repaints them).
BASE_POSE = ("idle", "blink", "blink_glitch", "grin")  # the same stance; every other clip is its own family
TAIL = 16
MAX_LINES = 8
MAX_CHARS = 2000  # a reply longer than this is cut; up to MAX_LINES show at once and the view scrolls along with the typing
WHEEL_LINES = 1  # lines one notch of the mouse wheel scrolls her bubble
MAX_DRAFT = 500

# Capri's clips run at 8 and 15 fps and she types 28 letters a second, so a coarse tick makes her uneven (at 40 ms a
# 15 fps frame was held 44 ms, then 88 ms, then 44 ...). About 60 ticks a second keeps every frame within a few ms of on time.
TICK_MS = 16
BLINK_TICKS = round(480 / TICK_MS)  # cursor blink period / 2 (~0.5 s)
POINTER_TICKS = round(200 / TICK_MS)  # sample the mouse pointer about every 200 ms
Z_TICKS = round(480 / TICK_MS)  # the sleeping "z z z" gains one letter every ~0.5 s
Z_CYCLE = (1, 2, 3, 3, 3)  # letters shown over one cycle; it rests on the full "z z z"

BTN_Y = 602
PET_DELAY_S = 1.3  # she grins first, then says a pet line
CONFIRM_TIMEOUT_S = 60  # an unanswered confirmation counts as "no"
LOADING = "Loading… (first run builds a sprite cache, about 20 s)"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _tail_span(x1: float, x2: float, cut: float, tip_x: float, tail: float) -> tuple[float, float]:
    """Where the pointer tail meets the bubble's bottom edge."""
    return max(x1 + cut + 6, tip_x - tail * 0.8), min(x2 - 6, tip_x + tail * 0.2)


def _bubble_rects(left: int, top: int, right: int, bottom: int, cut: int = 10) -> list[tuple[int, int, int, int]]:
    """Rectangles matching the bubble outline (cut left corners, 2 px outline), without its tail."""
    rects = [(left - 1, top + cut, right - left + 2, bottom - top - 2 * cut + 1)]
    for i in range(cut + 1):  # the diagonal corners, a row at a time
        x = left + cut - i - 1
        rects.append((x, top + i - 1, right + 1 - x, 1))
        rects.append((x, bottom - i + 1, right + 1 - x, 1))
    return rects


def _pill_rects(x1: int, y1: int, x2: int, y2: int, radius: int = 18) -> list[tuple[int, int, int, int]]:
    """Rectangles matching a pill with rounded ends."""
    radius = min(radius, (y2 - y1) // 2)
    rects = [(x1, y1 + radius, x2 - x1, y2 - y1 - 2 * radius)]
    for i in range(radius):
        inset = round(radius - math.sqrt(max(0.0, radius * radius - (radius - i - 0.5) ** 2)))
        rects.append((x1 + inset, y1 + i, x2 - x1 - 2 * inset, 1))
        rects.append((x1 + inset, y2 - 1 - i, x2 - x1 - 2 * inset, 1))
    return rects


def _plate(x1: float, y1: float, x2: float, y2: float, cut: float, tip_x: float, tail: float) -> list[float]:
    """Bubble outline: left corners cut, pointer tail on the bottom edge toward tip_x."""
    near, far = _tail_span(x1, x2, cut, tip_x, tail)
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
        self._home_x = 0  # where she sits when fully out; hide-and-seek moves the window around this
        self._home_y = 0
        self._screen_w = self.root.winfo_screenwidth()
        self._hide_offset = 0.0  # how far she has slid towards the nearest edge right now, in pixels
        self._applied_position: tuple[int, int] | None = None
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
        self._think_text: str | None = None  # "THINKING… N s" once the wait gets long, or what she is doing ("SEARCHING THE WEB…")
        self._doing = ""  # what she is doing right now, from the assistant (babyai/activity.py); only while she is thinking
        self._activity_lines: queue.Queue[str] = queue.Queue()  # filled from the worker thread, read on the Tk thread
        self._input_open = False
        self._tick_n = 0
        self._tick_due = time.monotonic()  # when the next tick should run (see _tick_delay)
        self._busy = False
        self._results: queue.Queue[str] = queue.Queue()
        self._confirm_requests: queue.Queue[dict] = queue.Queue()
        self._confirm: dict | None = None  # the confirmation being asked right now
        self._drag: tuple[int, int] | None = None
        self._pressed = False
        self._poll_id: str | None = None
        self._tick_id: str | None = None
        self._timings = timings_from_config(getattr(assistant, "config", None))
        hide_timings = hide_timings_from_config(getattr(assistant, "config", None))
        self._game = HideAndSeek(time.monotonic(), hide_timings) if hide_timings is not None else None
        self._clock = SleepClock(time.monotonic(), self._timings.sleep_after_s, self._timings.wake_radius_px)
        self._last_pointer: tuple[int, int] | None = None
        self._look = (0, 0)  # how far the eyes are slid toward the pointer, in pixels
        self._face_drawn: tuple | None = None  # what the stick figure's eyes and "z z z" currently show
        self._reveal_start = time.monotonic()  # when the current reply began typing out
        self._name = apply_character(assistant)
        self._sprites: SpriteBank | None = None  # Capri's sprites; None -> stick figure
        self._animator: Animator | None = None
        self._body_drawn: tuple | None = None  # (clip, cell, mouth) currently on the canvas
        self._body_item = self._mouth_item = 0
        self._pet = PetDetector()  # re-made with a scaled step once the sprites are loaded
        self._pet_count = 0
        self._pet_due: float | None = None
        self._bubble_bottom = BUBBLE_BOTTOM
        self._bubble_box: tuple[int, int, int, int] | None = None  # left, top, right, bottom of the bubble (not the tail); None while hidden
        self._bubble_hidden = False  # hides a while after she finishes talking (see BubbleTimer)
        self._bubble_timer = BubbleTimer(self._timings.bubble_hide_s)
        self._scroll: int | None = None  # first line of a long reply the reader scrolled to; None = follow the typing
        self._view = (0, 1, 1)  # (first line shown, lines in the reply, lines visible) as last drawn
        self._body_y = SPRITE_BOTTOM  # where her frame sits right now (it breathes)
        self._shape: XShape | None = None  # clips the window to her outline on X11
        self._shape_key: tuple | None = None
        ui = (getattr(assistant, "config", None) or {}).get("ui", {})
        self._sounds = SoundPlayer.from_config(ui)  # little sounds: off unless [ui] sounds = true
        self._hidden = False  # sent away with `python3 -m babyai --toggle` (see summon.py)
        self._summons: queue.Queue[str] = queue.Queue()
        self._summon_server: SummonServer | None = None
        self._history = InputHistory()  # what was typed to her, for the Up and Down keys
        self._typing_via_box = needs_typing_box(ui.get("typing", "auto"), self._windowing, os.environ)
        self._typing_box: TypingBox | None = None  # catches the keys when the borderless window can't (see typing_box.py)

        self._make_fonts()
        self._load_character()
        self._clock.touch(time.monotonic())  # a slow first load must not count as idle time
        self._draw_figure()
        self._build_controls()
        self._bind_events()
        self.assistant.confirm = self._confirm_from_worker  # the safety gate asks through the bubble
        self.assistant.activity = self._activity_lines.put  # and she says what she is doing while a tool runs
        self._set_reply(f"Hi, I'm {self._name}. Pencil to type, wave to talk.")
        self._poll_results()
        self._tick()
        if ui.get("summon", True) is True:
            server = SummonServer(socket_path(), self._summons)
            self._summon_server = server if server.start() else None  # (None: another overlay already answers, or no place for the socket)

        if not self.transparent:
            self.root.update()  # map the window first
            if not self._enable_shape():
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

    def _enable_shape(self) -> bool:
        """On X11 (and XWayland) clip the window to her outline, bubble and pill, so she floats on
        the desktop instead of sitting on a panel. Quietly does nothing - and the panel stays - if
        any part of that is unavailable."""
        if self._windowing != "x11" or self._sprites is None or self._body_drawn is None:
            return False
        if self._sprites.silhouette(*self._body_drawn[:2]) is None:
            return False
        try:
            ids = {self.root.winfo_id(), int(self.root.wm_frame(), 16)}
        except (tk.TclError, ValueError):
            ids = {self.root.winfo_id()}
        shape = XShape.create(ids)
        if shape is None:
            return False
        self._shape = shape
        for clip in CLIPS:  # work out every pose's outline now, not in the middle of an animation
            self._sprites.silhouette_union(self._pose_family(clip), grow_x=0, grow_y=self._breathing_room())
        self._update_shape()
        return True

    def _breathing_room(self) -> int:
        """Pixels of margin above and below her outline for her breathing (she only moves up and down)."""
        bank = self._sprites
        return max(1, round(2.2 * bank.zoom / bank.sub))

    def _pose_family(self, clip: str) -> tuple[str, ...]:
        return BASE_POSE if clip in BASE_POSE else (clip,)

    def _update_shape(self) -> None:
        shape = self._shape
        if shape is None or self._body_drawn is None or self._sprites is None:
            return
        if self._bubble_box is None and not self._bubble_hidden:
            return  # not drawn yet
        pill = self.canvas.bbox("pill")
        family = self._pose_family(self._body_drawn[0])
        key = (family, self._bubble_box, pill)  # not the frame or the breathing offset: those must not re-cut the shape
        if key == self._shape_key:
            return
        bank = self._sprites
        outline = bank.silhouette_union(family, grow_x=0, grow_y=self._breathing_room())
        if outline is None:
            self._drop_shape()
            return
        width, height = bank.cell_size
        ox, oy = (WIDTH - width) // 2, SPRITE_BOTTOM - height
        rects = [(max(0, ox + x), max(0, oy + y), w, h) for x, y, w, h in outline]
        if self._bubble_box is not None:  # (none while she has hidden it)
            left, top, right, bottom = self._bubble_box
            rects += _bubble_rects(left, top, right, bottom)  # the bubble itself ...
            near, far = _tail_span(BUBBLE_L, BUBBLE_R, 10, WIDTH // 2, TAIL)
            for step in range(TAIL):  # ... and its tail, row by row
                x1 = near + (WIDTH // 2 - near) * step / TAIL
                x2 = far - (far - WIDTH // 2) * step / TAIL
                rects.append((int(x1) - 1, bottom + step + 1, int(x2 - x1) + 3, 1))
        if pill:
            rects += _pill_rects(*pill)
        try:
            shape.set_rects([(max(0, x), max(0, y), min(w, WIDTH - max(0, x)), h) for x, y, w, h in rects])
        except Exception:
            self._drop_shape()
            return
        self._shape_key = key

    def _drop_shape(self) -> None:
        """Give up on the silhouette: show the whole window again, translucent like before."""
        shape, self._shape = self._shape, None
        if shape is not None:
            try:
                shape.set_rects([(0, 0, WIDTH, HEIGHT)])
                shape.close()
            except Exception:
                pass
        self._apply_panel_alpha()

    def _place_window(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - WIDTH - 48)
        y = max(0, screen_h - HEIGHT - 72)
        self._screen_w = screen_w
        self._home_x, self._home_y = x, y
        self._applied_position = (x, y)
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
        return self._name

    def _load_character(self) -> None:
        """Load Capri's sprites if they are there; otherwise the stick figure is drawn."""
        ui = (getattr(self.assistant, "config", None) or {}).get("ui", {})
        if str(ui.get("character", "capri")).strip().lower() != "capri":
            return
        bank = SpriteBank(self.root, scale=ui.get("sprite_scale", DEFAULT_SCALE))
        if not bank.available():
            print("Capri's sprites were not found under capri/assets/sprites; using the stick figure.")
            return
        clips = tuple(CLIPS)
        if not bank.cached(clips):
            print("Preparing Capri's sprites (first run only, about 20 seconds)...")
        self.canvas.create_text(WIDTH // 2, 330, text=LOADING, fill=DIM, font=self.font_mono, tags="loading")
        self.root.update()  # show the window before the (possibly slow) first load
        missing = bank.preload(clips)
        self.canvas.delete("loading")
        if any(name in missing for name in ("idle", "blink", "mouth")):
            print("Capri's sprites could not be loaded; using the stick figure.")
            return
        bank.warm()
        self._sprites = bank
        self._animator = Animator(time.monotonic())
        self._pet = PetDetector(min_step_px=max(3.0, 6.0 * bank.zoom / bank.sub))

    # ----------------------------------------------------------------- drawing

    def _rounded_rect(self, x1: float, y1: float, x2: float, y2: float, r: float, **kw) -> int:
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kw)

    def _draw_figure(self) -> None:
        if self._sprites is None:
            self._draw_stick_figure()
            return
        width, height = self._sprites.cell_size
        # The bubble's tail points at the top of her hair, whatever the sprite scale is.
        self._bubble_bottom = SPRITE_BOTTOM - height + int(height * SPRITE_HAIR) - TAIL - 4
        self._body_item = self.canvas.create_image(WIDTH // 2, SPRITE_BOTTOM, anchor="s", tags="figure")
        self._mouth_item = self.canvas.create_image(WIDTH // 2, SPRITE_BOTTOM, anchor="s", tags="figure")
        self._body_y = SPRITE_BOTTOM
        self._update_body(time.monotonic())

    def _speaking(self) -> bool:
        return bool(self._reply) and not self._thinking and self._shown < self._target

    def _update_body(self, now: float) -> None:
        """Advance Capri's animation and redraw her only when the frame (or her breathing) changed."""
        if self._sprites is None or self._animator is None:
            return
        animator, bank, c = self._animator, self._sprites, self.canvas
        animator.set_asleep(self._clock.asleep, now)
        speaking = self._speaking()
        mouth = mouth_cell(self._reply, now - self._reveal_start) if speaking else -1
        quiet = not (speaking or self._thinking or self._input_open or self._confirm)
        frame = animator.frame(now, quiet, mouth)
        key = (frame.clip, frame.cell, frame.mouth)
        if key != self._body_drawn:
            self._body_drawn = key
            body = bank.cell(frame.clip, frame.cell)
            if body is not None:
                c.itemconfigure(self._body_item, image=body)
            jaw = bank.cell("mouth", frame.mouth) if frame.mouth >= 0 else None
            c.itemconfigure(self._mouth_item, image=jaw if jaw is not None else "")
        y = SPRITE_BOTTOM + round(frame.breathe * bank.zoom / bank.sub)
        if y != self._body_y:  # moving an item makes Tk repaint it, so only do it when she actually moved
            self._body_y = y
            c.coords(self._body_item, WIDTH // 2, y)
            c.coords(self._mouth_item, WIDTH // 2, y)

    def _body_hit(self, x: int, y: int) -> bool:
        """Is the pointer over her (not over the see-through part of her frame)?"""
        if self._sprites is None or self._body_drawn is None:
            return False
        width, height = self._sprites.cell_size
        clip, cell, _mouth = self._body_drawn
        return self._sprites.is_opaque(clip, cell, x - (WIDTH - width) // 2, y - (SPRITE_BOTTOM - height))

    def _draw_stick_figure(self) -> None:
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
        """Redraw the stick figure's eyes and sleeping "z z z" only when what they show has changed.
        (Capri's sprites draw their own eyes and sleep, so this is for the stick figure only.)"""
        if self._sprites is not None:
            return
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

    def _wake(self, chime: bool = True) -> None:
        """A click, key press or reply counts as activity: wake up and restart the idle timer.
        (``chime=False`` when what woke her has a sound of its own: a reply, a question.)"""
        if self._hidden:  # whatever woke her (a reply, a question) brings her back
            self._show()
        now = time.monotonic()
        woke = self._clock.touch(now)
        if woke and chime:
            self._sounds.play("wake")
        if self._game is not None and self._game.attention(now) and self._animator is not None and not self._clock.asleep:
            self._animator.pet(now)  # a grin when she is found
        self._sync_face()

    def _presence_tick(self) -> None:
        """Fall asleep after a long idle spell; wake when the pointer moves close to the head."""
        now = time.monotonic()
        if self._busy and not self._clock.manual:  # waiting on a reply is not idleness
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
                if self._in_bubble(px - self.root.winfo_rootx(), py - self.root.winfo_rooty()):
                    self._bubble_timer.touch()  # resting the pointer on her bubble keeps it up
        was_asleep = self._clock.asleep
        if not self._clock.tick(now, distance, moved) and was_asleep:  # the pointer came close and woke her
            self._sounds.play("wake")
        self._sync_face()

    def _build_controls(self) -> None:
        c = self.canvas
        cx = WIDTH // 2
        x1, x2 = cx - 50, cx + 50
        self._rounded_rect(x1, BTN_Y - 18, x2, BTN_Y + 18, 18, fill=PILL_FILL, outline=PILL_EDGE, width=2, tags="pill")
        c.create_line((x1 + x2) / 2, BTN_Y - 9, (x1 + x2) / 2, BTN_Y + 9, fill=PILL_EDGE, width=1, tags="pill")
        self._make_button(x1 + 25, BTN_Y, "pencil", self._toggle_input, self._draw_pencil, r=15)
        self._make_button(x2 - 25, BTN_Y, "voice", self._on_mic, self._draw_wave, r=15)

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

    # ------------------------------------------------------------ speech bubble

    def _set_reply(self, text: str, thinking: bool = False) -> None:
        self._reply = text
        self._thinking = thinking
        self._think_since = time.monotonic() if thinking else None
        self._think_text = None
        self._doing = ""
        self._shown = 0  # the reply types itself out from the start
        self._scroll = None
        self._bubble_timer.touch()
        self._reveal_start = time.monotonic()
        if self._animator is not None and text and not thinking:
            self._animator.speak(self._reveal_start)  # talking cuts off a sleep, glitch or grin
        self._render_bubble()

    def _render_bubble(self) -> None:
        """Redraw the transcript bubble. It is laid out from y=0, then moved so its
        bottom edge sits on self._bubble_bottom (it grows upward, tail pointing at the figure)."""
        c = self.canvas
        c.delete("bubble")
        L, R = BUBBLE_L, BUBBLE_R
        x0 = L + 26
        cw, lh = self.char_w, self.line_h
        cols = max(12, int((R - 16 - x0) // cw))
        label = f"{self._assistant_name().upper()}:"
        label_px = self.font_label.measure(label) + cw  # the reply starts one space after the label
        label_cols = -(-label_px // cw)  # ceil

        self._bubble_hidden = False  # anything that draws the bubble wants it on screen
        reply = self._reply if len(self._reply) <= MAX_CHARS else self._reply[: MAX_CHARS - 1] + "…"
        lines = _wrap(reply, cols - label_cols, cols) if reply else [""]
        starts, count = [], 0
        for line in lines:
            starts.append(count)
            count += len(line) + 1
        self._target = count
        visible = min(len(lines), MAX_LINES)
        top = scroll_top(len(lines), visible, cursor_line([len(line) for line in lines], self._shown), self._scroll)
        self._view = (top, len(lines), visible)

        has_echo = self._input_open or bool(self._echo)
        pad = 12
        height = pad * 2 + (visible + (1 if has_echo else 0) + (1 if self._confirm else 0)) * lh
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

        if top == 0:  # her name labels the start of what she says; it scrolls away with it
            c.create_text(x0, y, anchor="nw", text=label, font=self.font_label, fill=RED, tags="bubble")
        if self._thinking:
            tx = x0 + label_px
            if self._think_text:
                c.create_text(tx, y, anchor="nw", text=self._think_text, font=self.font_mono, fill=DIM, tags="bubble")
                tx += self.font_mono.measure(self._think_text) + cw
            if blink_on:
                self._block(tx, y)
        else:
            for i in range(top, top + visible):
                line = lines[i]
                lx = x0 + (label_px if i == 0 else 0)
                ly = y + (i - top) * lh
                remaining = self._shown - starts[i]
                part = line[: max(0, remaining)]
                if part:
                    c.create_text(lx, ly, anchor="nw", text=part, font=self.font_mono, fill=REPLY_FG, tags="bubble")
                if remaining < len(line) + 1:  # the reveal is still in this line
                    if self._shown < self._target:
                        self._block(lx + self.font_mono.measure(part), ly)
                    break
            mark_x, first_y = R - 9, y  # in the right-hand margin, clear of the text
            if top > 0:  # more above / below than fits: small arrows at the right edge
                self._arrow(mark_x, first_y + 8, up=True)
            if top + visible < len(lines) and self._shown >= starts[top + visible]:  # typed text below the window (not text still to come)
                self._arrow(mark_x, first_y + visible * lh - 8, up=False)

        if self._confirm:  # answer row: clickable, or press Y / N
            ay = height - pad - lh
            yes = c.create_text(x0, ay, anchor="nw", text="[Y] allow", font=self.font_label, fill=CYAN, tags=("bubble", "confirm", "confirm_yes"))
            nx = c.bbox(yes)[2] + cw * 3
            c.create_text(nx, ay, anchor="nw", text="[N] decline", font=self.font_label, fill=RED, tags=("bubble", "confirm", "confirm_no"))

        c.move("bubble", 0, self._bubble_bottom - height)
        self._bubble_box = (L, self._bubble_bottom - height, R, self._bubble_bottom)

    def _arrow(self, x: float, y: float, up: bool) -> None:
        d = 4
        points = (x - d, y + d, x + d, y + d, x, y - d) if up else (x - d, y - d, x + d, y - d, x, y + d)
        self.canvas.create_polygon(points, fill=DIM, outline="", tags="bubble")

    def _hide_bubble(self) -> None:
        """Take the speech bubble away (and its piece of the window shape) until she next has something to say."""
        self.canvas.delete("bubble")
        self._bubble_hidden = True
        self._bubble_box = None

    def _in_bubble(self, x: float, y: float) -> bool:
        box = self._bubble_box
        return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _on_wheel(self, event, direction: int | None = None) -> None:
        """Scroll a long reply with the mouse wheel over her bubble (up = earlier lines)."""
        if self._bubble_hidden or not self._in_bubble(event.x, event.y):
            return
        self._bubble_timer.touch()
        top, total, visible = self._view
        if total <= visible:
            return
        if direction is None:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        want = min(max(0, top - direction * WHEEL_LINES), total - visible)
        self._scroll = None if want >= total - visible else want  # back at the end: follow the typing again
        self._render_bubble()

    def _block(self, x: float, y: float) -> None:
        self.canvas.create_rectangle(
            x, y + 2, x + self.char_w * 0.8, y + self.line_h - 5, fill=NEON, outline="", tags="bubble"
        )

    def _tick(self) -> None:
        self._tick_n += 1
        dirty = False
        now = time.monotonic()
        if not self._thinking and self._shown < self._target:
            shown = max(self._shown, letters_revealed(now - self._reveal_start, self._target))
            dirty = shown != self._shown  # redraw the bubble only when a new letter has appeared
            self._shown = shown
        if self._pet_due is not None and now >= self._pet_due:
            self._pet_due = None
            if not (self._busy or self._input_open or self._thinking or self._confirm):
                self._echo = "pets you"
                self._set_reply(pet_line(self._pet_count))
                self._pet_count += 1
                dirty = False  # _set_reply already redrew the bubble
        if self._thinking and self._think_since is not None:
            text = thinking_label(now - self._think_since, self._timings.think_after_s, self._doing)
            if text != self._think_text:
                self._think_text = text
                dirty = True
        if self._tick_n % BLINK_TICKS == 0 and (self._input_open or self._thinking):
            dirty = True
        self._presence_tick()
        busy = self._thinking or self._busy or self._input_open or self._confirm is not None or self._shown < self._target
        self._hide_tick(now, busy)
        if self._bubble_timer.update(now, busy, self._bubble_hidden):
            self._hide_bubble()
            dirty = False
        self._update_body(now)
        if dirty:
            self._render_bubble()
        self._update_shape()
        self._tick_id = self.root.after(self._tick_delay(), self._tick)

    def _tick_delay(self) -> int:
        """Milliseconds until the next tick, counted from when it was due rather than from now, so the time a
        tick takes doesn't stretch the rhythm. If we fall behind we carry on from now instead of catching up in a burst."""
        now = time.monotonic()
        self._tick_due = max(self._tick_due + TICK_MS / 1000, now)
        return max(1, round((self._tick_due - now) * 1000))

    def _hide_tick(self, now: float, busy: bool) -> None:
        """Slide her behind the screen edge when she has been ignored, and back out when the game says so."""
        if self._game is None:
            return
        self._game.update(now, busy or self._clock.asleep)
        direction = 1 if self._home_x + WIDTH / 2 >= self._screen_w / 2 else -1  # slide off the nearest side
        target = direction * round(WIDTH * self._game.fraction())
        delta = target - self._hide_offset
        if abs(delta) <= 1:
            self._hide_offset = float(target)
        else:
            step = max(1.0, 900.0 * TICK_MS / 1000)  # about 900 px a second, so a hide takes under half a second
            self._hide_offset += max(-step, min(step, delta))
        x = self._home_x + round(self._hide_offset)
        position = (x, self._home_y)
        if position != self._applied_position:
            self._applied_position = position
            self.root.geometry(f"+{x}+{self._home_y}")

    # ------------------------------------------------------------- interaction

    def _toggle_input(self) -> None:
        if self._confirm is not None:  # answer her question first
            return
        if self._input_open:
            self._close_input()
        else:
            self._open_input()

    def _open_input(self) -> None:
        self._input_open = True
        self._draft = ""
        self._render_bubble()
        if not (self._typing_via_box and self._show_typing_box()):
            self.canvas.focus_force()

    def _close_input(self) -> None:
        self._input_open = False
        self._draft = ""
        self._hide_typing_box()
        self._render_bubble()
        self.root.focus_set()

    def _show_typing_box(self) -> bool:
        """Open the little window that takes the keys; what is typed there shows in her bubble. False if it can't be made."""
        self._hide_typing_box()
        box = None
        try:
            box = TypingBox(
                self.root, f"Talk to {self._assistant_name()}", MAX_DRAFT, self._typed_in_box, self._send, self._close_input,
                dict(bg=PANEL_BG, field=BUBBLE_FILL, text=REPLY_FG, accent=NEON, dim=DIM), self.font_echo, self._recall,
            )
            left, top, right, _bottom = self._bubble_box or (BUBBLE_L, 0, BUBBLE_R, 0)
            box.place_above(self.root.winfo_rootx() + (left + right) // 2, self.root.winfo_rooty() + top - 8)
        except tk.TclError:
            if box is not None:
                box.destroy()
            self._typing_via_box = False  # no second window possible here: keep typing in her bubble
            return False
        self._typing_box = box
        box.focus()
        return True

    def _hide_typing_box(self) -> None:
        box, self._typing_box = self._typing_box, None
        if box is not None:
            box.destroy()

    def _typed_in_box(self, text: str) -> None:
        if not self._input_open:
            return
        self._wake()
        self._draft = text
        self._history.edited(text)
        self._render_bubble()

    def _on_key(self, event) -> str | None:
        self._wake()
        if self._confirm is not None:
            if event.keysym in ("y", "Y", "Return", "KP_Enter"):
                self._answer_confirm(True)
            elif event.keysym in ("n", "N", "Escape"):
                self._answer_confirm(False)
            return "break"
        if not self._input_open:
            if event.keysym == "Escape" or ((event.state & 0x4) and event.keysym == "period"):  # Esc or Ctrl+. : that's enough of the reply
                if self._busy or self._bubble_box is None:
                    return None
                self._hide_bubble()
                return "break"
            return None
        if event.keysym in ("Return", "KP_Enter"):
            self._send()
        elif event.keysym == "Escape":
            self._close_input()
        elif event.keysym == "Up":
            self._recall(-1)
        elif event.keysym == "Down":
            self._recall(1)
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
        self._history.edited(self._draft)
        self._render_bubble()
        return "break"

    def _recall(self, direction: int) -> None:
        """Up (-1) / Down (+1) in the box: what was said to her before."""
        text = self._history.older(self._draft) if direction < 0 else self._history.newer()
        if text is None:
            return
        self._draft = text[:MAX_DRAFT]
        if self._typing_box is not None:
            self._typing_box.set_text(self._draft)
        self._render_bubble()

    def _send(self) -> None:
        text = self._draft.strip()
        if not text or self._busy:
            return
        self._input_open = False
        self._draft = ""
        self._hide_typing_box()
        self._submit(text)

    def _submit(self, text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._echo = text
        self._history.add(text)
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
                doing = self._activity_lines.get_nowait()
                if self._thinking:  # (a late word after the answer is ignored)
                    self._doing = doing
        except queue.Empty:
            pass
        try:
            while True:
                self._set_reply(self._results.get_nowait())
                self._busy = False
                self._wake(chime=False)
                self._sounds.play("reply")
        except queue.Empty:
            pass
        if self._confirm is None:
            try:
                self._begin_confirm(self._confirm_requests.get_nowait())
            except queue.Empty:
                pass
        elif time.monotonic() > self._confirm["deadline"]:
            self._answer_confirm(False)  # nobody answered: that is a no
        self._handle_summons()
        self._poll_id = self.root.after(100, self._poll_results)

    def _handle_summons(self) -> None:
        try:
            while True:
                word = self._summons.get_nowait()
                if word == "show" or (word == "toggle" and self._hidden):
                    self._show()
                    self._wake()
                    self.canvas.focus_force()
                elif word in ("hide", "toggle"):
                    self._hide()
        except queue.Empty:
            pass

    def _hide(self) -> None:
        """Send her away until she is summoned again, or has something to say. Not while she is waiting for a yes or no."""
        if self._hidden or self._confirm is not None:
            return
        if self._input_open:
            self._close_input()
        self._hidden = True
        self.root.withdraw()

    def _show(self) -> None:
        if not self._hidden:
            return
        self._hidden = False
        self.root.deiconify()
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.lift()

    def _on_mic(self) -> None:
        if self._busy or self._confirm is not None:
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

    # ------------------------------------------------------------ confirmation

    def _confirm_from_worker(self, name: str, arguments: dict, verdict: dict) -> bool:
        """Called on the worker thread by the safety gate; blocks until the user answers in the bubble."""
        shown = ", ".join(f"{key}={str(value)[:48]!r}" for key, value in arguments.items())
        request = {
            "text": f"Can I run {name}? {shown} ({getattr(verdict.get('risk_level'), 'value', verdict.get('risk_level', '?'))} risk)".replace("  ", " "),
            "event": threading.Event(),
            "answer": False,
            "deadline": 0.0,
        }
        self._confirm_requests.put(request)
        request["event"].wait(CONFIRM_TIMEOUT_S + 10)  # the Tk side times out first; this only covers a closed window
        return bool(request["answer"])

    def _begin_confirm(self, request: dict) -> None:
        request["deadline"] = time.monotonic() + CONFIRM_TIMEOUT_S
        self._confirm = request
        if self._typing_box is not None:  # her question comes first: the keys would otherwise go into the box
            self._hide_typing_box()
            self._input_open = False
            self._draft = ""
        self._wake(chime=False)
        self._sounds.play("ask")
        self._set_reply(request["text"])
        self.canvas.focus_force()

    def _answer_confirm(self, yes: bool) -> None:
        request, self._confirm = self._confirm, None
        if request is None:
            return
        request["answer"] = yes
        request["event"].set()
        self._set_reply("", thinking=True)  # she carries on with the job

    # ------------------------------------------------------------ drag / close

    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<ButtonPress-1>", self._start_drag)
        c.bind("<ButtonRelease-1>", self._end_press)
        c.bind("<B1-Motion>", self._move_window)
        c.bind("<Motion>", self._on_motion)
        c.bind("<MouseWheel>", self._on_wheel)  # Windows and macOS
        c.bind("<Button-4>", lambda event: self._on_wheel(event, 1))  # X11
        c.bind("<Button-5>", lambda event: self._on_wheel(event, -1))
        c.tag_bind("confirm_yes", "<Button-1>", lambda _event: self._answer_confirm(True))
        c.tag_bind("confirm_no", "<Button-1>", lambda _event: self._answer_confirm(False))
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
        self._pressed = True
        current = self.canvas.find_withtag("current")
        if current and {"btn", "confirm"} & set(self.canvas.gettags(current[0])):
            self._drag = None  # a button press, not a window drag
            return
        if self._input_open:
            self.canvas.focus_force()
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _end_press(self, _event) -> None:
        self._pressed = False
        self._pet.reset()

    def _on_motion(self, event) -> None:
        """Stroking the pointer back and forth across her pets her."""
        if self._animator is None:
            return
        now = time.monotonic()
        blocked = self._pressed or self._busy or self._input_open or self._clock.asleep or self._confirm is not None
        if self._pet.feed(event.x, now, self._body_hit(event.x, event.y), blocked):
            if self._game is not None:
                self._game.attention(now)  # stroking her counts as finding her
            self._animator.pet(now)
            self._sounds.play("pet")
            self._pet_due = now + PET_DELAY_S

    def _move_window(self, event) -> None:
        if self._drag is None:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self._home_x, self._home_y = x, y
        self._hide_offset = 0.0  # dragging her back into view counts as finding her
        self._applied_position = (x, y)
        self.root.geometry(f"+{x}+{y}")

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
        if self._summon_server is not None:
            self._summon_server.stop()
            self._summon_server = None
        self._hide_typing_box()
        if self._shape is not None:
            self._shape.close()
            self._shape = None
        pending, self._confirm = self._confirm, None
        if pending is not None:  # unblock a tool call that is waiting on an answer (the answer is "no")
            pending["event"].set()
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
