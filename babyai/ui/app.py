import tkinter as tk

from babyai.agent.core import BabyAI


class BabyAIDesktopWindow:
    def __init__(self, assistant: BabyAI, root: tk.Tk | None = None):
        self.assistant = assistant
        self.root = root or tk.Tk()
        self.root.title("BabyAI")
        self.root.geometry("420x640")
        self.root.minsize(360, 520)
        self.root.configure(bg="#0b0d12")
        self._build_ui()

    def _build_ui(self):
        main = tk.Frame(self.root, bg="#0b0d12", padx=12, pady=12)
        main.pack(fill="both", expand=True)

        header = tk.Frame(main, bg="#121821", padx=12, pady=12)
        header.pack(fill="x", pady=(0, 10))

        title = tk.Label(
            header,
            text="BabyAI",
            fg="#f8fafc",
            bg="#121821",
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            header,
            text="Local assistant",
            fg="#8ea0b5",
            bg="#121821",
            font=("Segoe UI", 9),
        )
        subtitle.pack(anchor="w")

        body = tk.Frame(main, bg="#0e1117", padx=10, pady=10)
        body.pack(fill="both", expand=True)

        self.chat_log = tk.Text(
            body,
            bg="#0e1117",
            fg="#f8fafc",
            wrap="word",
            font=("Segoe UI", 10),
            relief="flat",
            bd=0,
            padx=10,
            pady=10,
            state="disabled",
        )
        self.chat_log.pack(fill="both", expand=True)
        self.chat_log.tag_configure("user", foreground="#dbeafe", font=("Segoe UI", 10, "bold"))
        self.chat_log.tag_configure("assistant", foreground="#c7f9cc", font=("Segoe UI", 10, "bold"))
        self.chat_log.tag_configure("system", foreground="#9ca3af", font=("Segoe UI", 10))

        self._append_system("Ready. Ask BabyAI anything.")

        footer = tk.Frame(main, bg="#0b0d12", pady=8)
        footer.pack(fill="x")

        self.entry = tk.Entry(
            footer,
            bg="#111827",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 11),
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry.bind("<Return>", self._submit)

        send_btn = tk.Button(
            footer,
            text="Send",
            bg="#7c3aed",
            fg="#ffffff",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            command=self._submit_from_button,
        )
        send_btn.pack(side="right")

    def _append_system(self, message: str) -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"{message}\n\n", "system")
        self.chat_log.configure(state="disabled")
        self.chat_log.see("end")

    def _append_user(self, message: str) -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"You: {message}\n\n", "user")
        self.chat_log.configure(state="disabled")
        self.chat_log.see("end")

    def _append_assistant(self, message: str) -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"BabyAI: {message}\n\n", "assistant")
        self.chat_log.configure(state="disabled")
        self.chat_log.see("end")

    def _submit(self, _event=None):
        message = self.entry.get().strip()
        if not message:
            return
        self.entry.delete(0, "end")
        self._append_user(message)
        reply = self.assistant.process_user_input(message)
        self._append_assistant(reply)

    def _submit_from_button(self):
        self._submit()

    def run(self):
        self.root.mainloop()


def launch_desktop_ui(assistant: BabyAI):
    app = BabyAIDesktopWindow(assistant)
    app.run()


if __name__ == "__main__":
    launch_desktop_ui(BabyAI())
