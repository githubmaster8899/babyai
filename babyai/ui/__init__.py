import tkinter as tk

from babyai.agent.core import BabyAI


class BabyAIDesktopWindow:
    def __init__(self, assistant: BabyAI, root: tk.Tk | None = None):
        self.assistant = assistant
        self.root = root or tk.Tk()
        self.root.title("BabyAI")
        self.root.geometry("920x560")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#ffffff")

        try:
            self.root.attributes("-transparentcolor", "#ffffff")
        except tk.TclError:
            pass

        try:
            self.root.attributes("-alpha", 0.98)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.root,
            width=920,
            height=560,
            bg="#ffffff",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._draw_ui()
        self._bind_drag()

    def _bind_drag(self):
        self._drag_x = 0
        self._drag_y = 0
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._move_window)

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _move_window(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _draw_ui(self):
        self.canvas.create_rectangle(235, 80, 500, 150, fill="#091119", outline="#1f2c39", width=2)
        self.canvas.create_text(275, 114, text="> you:", fill="#f0f2f5", font=("Consolas", 16, "bold"))

        self.canvas.create_rectangle(180, 165, 700, 260, fill="#091119", outline="#1f2c39", width=2)
        self.canvas.create_text(
            385,
            212,
            text="> you: hi\nCAPRI: I can't reply through here\nchoom, you needa install my chip",
            fill="#f0f2f5",
            font=("Consolas", 16),
            justify="left",
        )

        cx = 500
        cy = 330
        self.canvas.create_oval(cx - 32, cy - 120, cx + 32, cy - 52, fill="#f4f7f9", outline="#171717", width=3)
        self.canvas.create_line(cx, cy - 52, cx, cy + 42, fill="#171717", width=8)
        self.canvas.create_line(cx, cy - 10, cx - 70, cy + 42, fill="#171717", width=8)
        self.canvas.create_line(cx, cy - 10, cx + 70, cy + 42, fill="#171717", width=8)
        self.canvas.create_line(cx, cy + 42, cx - 25, cy + 120, fill="#171717", width=8)
        self.canvas.create_line(cx, cy + 42, cx + 25, cy + 120, fill="#171717", width=8)

        self.canvas.create_line(cx - 6, cy - 30, cx - 55, cy + 18, fill="#ff2d7d", width=5)
        self.canvas.create_line(cx + 6, cy - 30, cx + 55, cy + 18, fill="#ff2d7d", width=5)
        self.canvas.create_line(cx - 8, cy + 12, cx - 40, cy + 72, fill="#ff2d7d", width=5)
        self.canvas.create_line(cx + 8, cy + 12, cx + 40, cy + 72, fill="#ff2d7d", width=5)

        self.canvas.create_rectangle(340, 465, 430, 525, fill="#1d2430", outline="#393d49", width=2)
        self.canvas.create_text(385, 495, text="✎", fill="#ffffff", font=("Segoe UI Symbol", 24))

        self.canvas.create_rectangle(455, 465, 545, 525, fill="#1d2430", outline="#393d49", width=2)
        self.canvas.create_text(500, 495, text="◉", fill="#ffffff", font=("Segoe UI Symbol", 24))

    def run(self):
        self.root.mainloop()


def launch_desktop_ui(assistant: BabyAI):
    app = BabyAIDesktopWindow(assistant)
    app.run()


if __name__ == "__main__":
    launch_desktop_ui(BabyAI())
