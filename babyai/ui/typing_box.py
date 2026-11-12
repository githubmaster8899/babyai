"""A small ordinary window that catches the keyboard when the overlay itself can't.

The overlay is a borderless window the window manager does not manage. On a Wayland desktop (the
default on KDE Plasma, where Tk runs through XWayland) such a window can be clicked but never gets
keyboard focus, so typing "in her bubble" does nothing. This box is a normal Tk window, which every
desktop gives focus to. What is typed in it is mirrored into her bubble by the overlay, so it reads
the same either way - the keys just arrive through here.

``[ui] typing`` in config.toml (``"auto"``, ``"box"`` or ``"bubble"``) decides whether it is used: see
``babyai.ui.behavior.needs_typing_box``.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Mapping


class TypingBox:
    def __init__(
        self,
        root: tk.Misc,
        title: str,
        limit: int,
        on_change: Callable[[str], None],
        on_send: Callable[[], None],
        on_cancel: Callable[[], None],
        colors: Mapping[str, str],
        font: object = None,
        on_history: Callable[[int], None] | None = None,
    ) -> None:
        self._on_change = on_change
        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.resizable(False, False)
        self.window.configure(bg=colors["bg"], padx=10, pady=10)
        try:
            self.window.attributes("-topmost", True)  # she is always on top, so the box has to be too
        except tk.TclError:
            pass

        self._var = tk.StringVar()
        self._limit = limit
        self.entry = tk.Entry(
            self.window, textvariable=self._var, width=46, bd=0, relief="flat", highlightthickness=2,
            bg=colors["field"], fg=colors["text"], insertbackground=colors["accent"],
            highlightbackground=colors["dim"], highlightcolor=colors["accent"],
        )
        if font is not None:
            self.entry.configure(font=font)
        self.entry.pack(ipady=5)
        self._trace = self._var.trace_add("write", self._changed)
        self.entry.bind("<Return>", lambda _event: (on_send(), "break")[1])
        self.entry.bind("<KP_Enter>", lambda _event: (on_send(), "break")[1])
        self.entry.bind("<Escape>", lambda _event: (on_cancel(), "break")[1])
        if on_history is not None:  # Up goes back through what was said before (-1), Down forward again (+1)
            self.entry.bind("<Up>", lambda _event: (on_history(-1), "break")[1])
            self.entry.bind("<Down>", lambda _event: (on_history(1), "break")[1])
        self.window.protocol("WM_DELETE_WINDOW", on_cancel)

    def _changed(self, *_args) -> None:
        text = self._var.get()
        if len(text) > self._limit:
            text = text[: self._limit]
            self._var.set(text)  # Tk does not run this trace again for a change made inside it
        self._on_change(text)

    def set_text(self, text: str) -> None:
        """Replace what is in the box (and the bubble, through the change callback), cursor at the end."""
        self._var.set(text)
        self.entry.icursor("end")

    def place_above(self, centre_x: int, bottom_y: int) -> None:
        """Put the box centred on ``centre_x`` with its bottom edge at ``bottom_y`` (screen pixels), kept on the screen."""
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = min(max(0, centre_x - width // 2), max(0, self.window.winfo_screenwidth() - width))
        y = min(max(0, bottom_y - height), max(0, self.window.winfo_screenheight() - height))
        self.window.geometry(f"+{x}+{y}")

    def focus(self) -> None:
        try:
            self.window.lift()
            self.entry.focus_force()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        try:
            self._var.trace_remove("write", self._trace)
        except tk.TclError:
            pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass
