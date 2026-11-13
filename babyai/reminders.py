"""Reminders and timers: "remind me to call mum at 15:30", "timer for 25 minutes".

They are kept in one small file (``reminders.json``, in a ``reminders`` folder next to the memory database) so they survive
a restart: the user can open, edit or delete it. A front end (the desktop bubble, the CLI) calls ``start`` and is handed the
text when one is due. Nothing here runs a command or sends the text to the model: it is only ever shown to the user.

A reminder is only delivered while BabyAI is running. One that came due while it was closed is shown the next time it starts,
with the time it was meant for.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

MAX_PENDING = 50
MAX_TEXT = 200
MAX_AHEAD_S = 366 * 24 * 3600  # a year: anything further off is far more likely a typo than a plan
LATE_NOTE_S = 120  # a reminder shown this much later than asked for says when it was due
POLL_S = 1.0

CLOCK = re.compile(r"^(?:(\d{4})-(\d{1,2})-(\d{1,2})[ t]+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


class ReminderError(ValueError):
    """Something the user (or the model) can be told in plain words."""


def parse_when(now: float, minutes: Any = None, at: Any = None) -> float:
    """When (epoch seconds) a reminder is due: ``minutes`` from now, or the clock time ``at`` ("15:30", "3:30 pm", "9pm",
    which means the next time it is that time; or "2026-12-24 18:00" for a date)."""
    has_minutes = minutes not in (None, "", 0, 0.0, "0")
    has_at = bool(str(at or "").strip())
    if has_minutes == has_at:
        raise ReminderError("Give either minutes (from now) or at (a clock time), not both and not neither.")
    if has_minutes:
        try:
            wait = float(minutes) * 60
        except (TypeError, ValueError):
            raise ReminderError(f"{minutes!r} isn't a number of minutes.") from None
        if not math.isfinite(wait) or wait <= 0:
            raise ReminderError("Minutes has to be more than zero.")
        due = now + wait
    else:
        match = CLOCK.match(str(at).strip().lower())
        if match is None:
            raise ReminderError(f"I can't read {at!r} as a time. Use something like 15:30, 3:30 pm or 2026-12-24 18:00.")
        year, month, day, hour, minute, meridiem = match.groups()
        if minute is None and meridiem is None:
            raise ReminderError(f"{at!r} could be an hour or a number of minutes. Say 15:00 or 3 pm.")
        hour, minute = int(hour), int(minute or 0)
        if meridiem:
            if not 1 <= hour <= 12:
                raise ReminderError(f"{at!r} isn't a time: with am/pm the hour is 1 to 12.")
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        if hour > 23 or minute > 59:
            raise ReminderError(f"{at!r} isn't a time of day.")
        try:
            if year:
                due = datetime(int(year), int(month), int(day), hour, minute).timestamp()
            else:
                today = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
                due = today.timestamp()
                if due <= now:
                    due = (today + timedelta(days=1)).timestamp()  # it is already past that time today: tomorrow
        except (ValueError, OverflowError):
            raise ReminderError(f"{at!r} isn't a real date and time.") from None
        if due <= now:
            raise ReminderError(f"{at!r} has already passed.")
    if due > now + MAX_AHEAD_S:
        raise ReminderError("That is more than a year away; I only keep reminders for the coming year.")
    return due


def clean_text(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    if not cleaned:
        raise ReminderError("What should I remind you about? (text is empty)")
    if len(cleaned) > MAX_TEXT:
        raise ReminderError(f"That reminder is too long ({len(cleaned)} characters; the limit is {MAX_TEXT}).")
    return cleaned


def _span(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def when_text(due: float, now: float) -> str:
    """"Thu 15:30 (in 25 min)": how a due time is told back to the user."""
    local = time.localtime(due)
    if due - now < 600:
        clock = time.strftime("%H:%M:%S", local)
    elif due - now < 20 * 3600:
        clock = time.strftime("%H:%M", local)
    else:
        clock = time.strftime("%a %d %b %H:%M", local)
    return f"{clock} (in {_span(due - now)})"


def _ident(ident: Any) -> int | None:
    """The id a model or a person gave ("3", 3, 3.0, "#3") as a number, or None."""
    text = str(ident).strip().lstrip("#")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if math.isfinite(number) and number.is_integer() else None


def _valid(item: Any) -> bool:
    try:
        return (
            isinstance(item["id"], int) and not isinstance(item["id"], bool) and item["id"] >= 1
            and isinstance(item["text"], str) and 0 < len(item["text"]) <= MAX_TEXT
            and all(isinstance(item[key], (int, float)) and not isinstance(item[key], bool) and math.isfinite(item[key]) for key in ("due", "created"))
        )
    except (KeyError, TypeError):
        return False


class Reminders:
    def __init__(self, directory: str | os.PathLike, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(directory).expanduser() / "reminders.json"
        self._clock = clock
        self._lock = threading.RLock()
        self._served: set[int] = set()  # shown, but taking them off the file failed: never shown twice in one run
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._deliver: Callable[[str], None] | None = None
        self._ready: Callable[[], bool] = lambda: True

    def now(self) -> float:
        return self._clock()

    # ------------------------------------------------------------------ the file

    def _load(self) -> tuple[int, list[dict[str, Any]]]:
        try:
            raw = json.loads(self.path.read_text())
            items = [dict(item) for item in raw["items"] if _valid(item)]
            next_id = raw["next_id"]
            if not isinstance(next_id, int) or isinstance(next_id, bool):
                raise TypeError
        except FileNotFoundError:
            return 1, []
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            try:  # damaged or from something else: put it aside rather than silently throw the user's reminders away
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass
            return 1, []
        seen: set[int] = set()
        kept = []
        for item in items:
            if item["id"] not in seen and item["id"] not in self._served:
                seen.add(item["id"])
                kept.append(item)
        return max([next_id, *(i["id"] + 1 for i in items)]), kept

    def _save(self, next_id: int, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"version": 1, "next_id": next_id, "items": items}, indent=1))
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    # ------------------------------------------------------------------ what the tools do

    def add(self, text: Any, minutes: Any = None, at: Any = None) -> dict[str, Any]:
        now = self._clock()
        text = clean_text(text)
        due = parse_when(now, minutes, at)
        with self._lock:
            next_id, items = self._load()
            for item in items:  # a model that asks twice must not set it twice
                if item["text"] == text and abs(item["due"] - due) < 60:
                    return {**item, "duplicate": True}
            if len(items) >= MAX_PENDING:
                raise ReminderError(f"I'm already holding {MAX_PENDING} reminders. Cancel some first.")
            item = {"id": next_id, "text": text, "due": due, "created": now}
            items.append(item)
            try:
                self._save(next_id + 1, items)
            except OSError as exc:
                raise ReminderError(f"I couldn't save it ({exc}), so it would be lost.") from None
            return dict(item)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._load()[1], key=lambda item: (item["due"], item["id"]))

    def find(self, ident: Any) -> dict[str, Any] | None:
        wanted = _ident(ident)
        return next((item for item in self.pending() if item["id"] == wanted), None)

    def cancel(self, ident: Any) -> dict[str, Any]:
        with self._lock:
            next_id, items = self._load()
            wanted = _ident(ident)
            found = next((item for item in items if item["id"] == wanted), None)
            if found is None:
                raise ReminderError(f"There is no reminder {ident!r}." + (" Waiting: " + ", ".join(f"{i['id']} ({i['text'][:30]})" for i in items) if items else " None are waiting."))
            items.remove(found)
            try:
                self._save(next_id, items)
            except OSError as exc:
                raise ReminderError(f"I couldn't update the file ({exc}), so it is still there.") from None
            return found

    # ------------------------------------------------------------------ delivering

    def take_due(self, now: float | None = None) -> list[dict[str, Any]]:
        """The reminders that are due, oldest first, taken off the file (so they are shown once)."""
        now = self._clock() if now is None else now
        with self._lock:
            next_id, items = self._load()
            due = sorted((item for item in items if item["due"] <= now), key=lambda item: (item["due"], item["id"]))
            if not due:
                return []
            try:
                self._save(next_id, [item for item in items if item not in due])
            except OSError:
                self._served.update(item["id"] for item in due)  # (can't take them off the file: at least show them only once)
            return due

    def put_back(self, due: list[dict[str, Any]]) -> None:
        """Delivery failed: keep them, to try again."""
        with self._lock:
            next_id, items = self._load()
            for item in due:
                self._served.discard(item["id"])
                if all(item["id"] != other["id"] for other in items):
                    items.append(item)
            try:
                self._save(max(next_id, *(i["id"] + 1 for i in items)), items)
            except OSError:
                pass

    @staticmethod
    def message(due: list[dict[str, Any]], now: float) -> str:
        def one(item: dict[str, Any]) -> str:
            late = now - item["due"]
            return item["text"] + (f" (that was for {time.strftime('%H:%M', time.localtime(item['due']))})" if late > LATE_NOTE_S else "")

        return ("Reminder: " if len(due) == 1 else "Reminders: ") + "; ".join(one(item) for item in due)

    def start(self, deliver: Callable[[str], None], ready: Callable[[], bool] | None = None) -> None:
        """Hand each due reminder (as a line of text) to ``deliver``, from a background thread. ``ready`` says whether the front
        end can show one right now (a reminder waits on the file until it can). Calling it again swaps the two."""
        with self._lock:
            self._deliver = deliver
            self._ready = ready or (lambda: True)
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="babyai-reminders")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.deliver_due()
            except Exception:  # (a bad moment for one poll must not end the watching)
                pass
            self._stop.wait(POLL_S)

    def deliver_due(self) -> int:
        """One poll: hand over whatever is due. Returns how many reminders were delivered."""
        deliver, ready = self._deliver, self._ready
        if deliver is None or not ready():
            return 0
        now = self._clock()
        due = self.take_due(now)
        if not due:
            return 0
        try:
            deliver(self.message(due, now))
        except Exception:
            self.put_back(due)
            raise
        return len(due)
