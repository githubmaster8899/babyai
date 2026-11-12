"""Show or hide the running overlay from outside: ``python3 -m babyai --toggle`` (also ``--show`` and ``--hide``).

Bind ``python3 -m babyai --toggle`` to a keyboard shortcut (KDE: System Settings -> Keyboard -> Shortcuts -> Add Command) and one key
press brings her up or sends her away. An idea from Desktop Copilot's summon hotkey; on Wayland an app can't grab a global key by
itself, so the desktop's own shortcut does the grabbing and this is the part it talks to.

The overlay listens on a unix socket in the user's runtime folder (``$XDG_RUNTIME_DIR``, otherwise a private folder in /tmp), made
readable and writable by this user only. The only things it understands are the three words above; it never runs anything it is sent.
Display-free, so it can be tested without Tk.
"""

from __future__ import annotations

import os
import queue
import socket
import tempfile
import threading
from pathlib import Path
from typing import Mapping

COMMANDS = ("toggle", "show", "hide")
MAX_MESSAGE = 32  # bytes: the longest word is 6
CLIENT_TIMEOUT_S = 2.0
CLIENT_PATIENCE_S = 1.0  # a client that connects and says nothing is dropped after this long


def socket_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "babyai-overlay.sock"
    return Path(tempfile.gettempdir()) / f"babyai-{os.getuid()}" / "overlay.sock"


def send(command: str, path: Path | None = None, timeout: float = CLIENT_TIMEOUT_S) -> str | None:
    """Tell the running overlay (``toggle``, ``show`` or ``hide``). Its answer ("ok" or "unknown"), or None if nobody is listening."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path or socket_path()))
        client.sendall(command.encode("ascii", "ignore")[:MAX_MESSAGE] + b"\n")
        return client.recv(MAX_MESSAGE).decode("ascii", "ignore").strip() or None
    except OSError:  # nothing there, a stale file, or no answer in time
        return None
    finally:
        client.close()


class SummonServer:
    """Listens in a thread of its own and hands each word it understands to ``commands`` (the Tk side reads it from there)."""

    def __init__(self, path: Path, commands: "queue.Queue[str]") -> None:
        self.path = path
        self._commands = commands
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> bool:
        """False if it can't listen: another overlay already does, or there is nowhere to put the socket. Never raises."""
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.exists() or self.path.is_symlink():
                if send("", self.path, timeout=0.5) is not None:  # somebody answers: it is not stale
                    return False
                self.path.unlink()  # a leftover from an overlay that did not close properly
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(str(self.path))
                os.chmod(self.path, 0o600)
                server.listen(16)  # (a full queue makes a client's connect fail at once, so it is not small)
                server.settimeout(0.5)
            except OSError:
                server.close()
                raise
        except OSError:
            return False
        self._socket = server
        self._thread = threading.Thread(target=self._serve, daemon=True, name="babyai-summon")
        self._thread.start()
        return True

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stopping.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                self._answer(connection)

    def _answer(self, connection: socket.socket) -> None:
        connection.settimeout(CLIENT_PATIENCE_S)
        try:
            word = connection.recv(MAX_MESSAGE).decode("ascii", "ignore").strip().lower()
            if word in COMMANDS:
                self._commands.put(word)
                connection.sendall(b"ok\n")
            else:
                connection.sendall(b"unknown\n")
        except OSError:
            pass

    def stop(self) -> None:
        self._stopping.set()
        server, self._socket = self._socket, None
        if server is not None:
            server.close()
            try:
                self.path.unlink()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None
