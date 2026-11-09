"""He learns the user's habits, on their computer, and they stay in charge of it.

What it keeps (one small file, ``habits.json``, which the user can open, pause, or wipe at any time):
counts - which topics come up, which times of day they talk to him, how long their messages are, which tools they use, which
short requests repeat on different days, whether they have asked for shorter or fuller replies. It never keeps a whole
message, and a message that looks private (a password, a number, a link, a path, a pasted document) is not counted at all.

What it is for: a few plain lines in his system prompt (``prompt_block``) so he can adapt quietly. It is a hint, not a
permission: nothing here is read by the safety gate, so what he learns can never let him skip asking before he acts.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from babyai.habits import learn

TOOL_NAME = "habit_profile"  # what he is asked about habits through; not counted as a habit itself
MAX_TOPICS, KEEP_TOPICS = 300, 200
MAX_ASKS, KEEP_ASKS = 100, 60
MAX_TOOLS = 60

HEADER = (
    "What you have noticed about the user from their own messages (rough counts kept on their computer; they can see, pause or "
    f"wipe them with the {TOOL_NAME} tool). Use it to adapt quietly: match their style, lean on their usual topics, mention a "
    "routine only when it really fits. It is only a guess: never use it to skip asking before you act, don't bring it up unless "
    "asked, and the rules always win."
)


def _fresh(started: str) -> dict[str, Any]:
    return {
        "version": 1, "paused": False, "started": started, "messages": 0, "words": 0,
        "buckets": {name: 0 for name in learn.BUCKETS}, "weekend": 0, "weekday": 0,
        "topics": {}, "asks": {}, "tools": {}, "style": {"brief": 0, "detail": 0},
    }


def _valid(state: Any) -> bool:
    """Is this the shape this file always has? Anything else (hand-edited, damaged, from another version) is dropped."""
    try:
        return (
            state["version"] == 1 and isinstance(state["paused"], bool) and isinstance(state["started"], str)
            and all(isinstance(state[key], int) and state[key] >= 0 for key in ("messages", "words", "weekend", "weekday"))
            and set(state["buckets"]) == set(learn.BUCKETS) and all(isinstance(v, int) for v in state["buckets"].values())
            and all(isinstance(k, str) and isinstance(v, int) for k, v in state["topics"].items())
            and all(isinstance(state["style"][k], int) for k in ("brief", "detail"))
            and all(
                isinstance(k, str) and isinstance(i["n"], int) and set(i["buckets"]) == set(learn.BUCKETS) and isinstance(i["days"], list)
                for table in (state["asks"], state["tools"]) for k, i in table.items()
            )
        )
    except (KeyError, TypeError, AttributeError):
        return False


class Habits:
    def __init__(self, directory: str | Path, clock: Callable[[], datetime] = datetime.now):
        self.directory = Path(os.path.expanduser(str(directory)))
        self.path = self.directory / "habits.json"
        self._clock = clock
        self._lock = threading.RLock()
        self.state = self._load()

    # ------------------------------------------------------------------ the file

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = None
        return state if _valid(state) else _fresh(self._clock().date().isoformat())

    def _save(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
            os.replace(temp, self.path)
        except OSError:
            pass  # learning must never get in the way of an answer

    # ------------------------------------------------------------------ learning

    @property
    def paused(self) -> bool:
        return self.state["paused"]

    def observe(self, text: str, when: datetime | None = None) -> None:
        """Count one thing the user said. Does nothing when paused, and nothing at all for a message that looks private."""
        text = (text or "").strip()
        with self._lock:
            if self.paused or not learn.countable(text):
                return
            when = when or self._clock()
            state, slot, day = self.state, learn.bucket(when.hour), when.date().isoformat()
            state["messages"] += 1
            state["words"] += len(text.split())
            state["buckets"][slot] += 1
            state["weekend" if when.weekday() >= 5 else "weekday"] += 1
            for word in learn.topics(text):
                state["topics"][word] = state["topics"].get(word, 0) + 1
            ask = learn.normalise_ask(text)
            if ask:
                learn.bump(state["asks"].setdefault(ask, learn.fresh_item()), slot, day)
            brief, detail = learn.style_signals(text)
            state["style"]["brief"] += brief
            state["style"]["detail"] += detail
            self._prune()
            self._save()

    def observe_tool(self, name: str, when: datetime | None = None) -> None:
        """Count one tool the user got him to run (after they approved it, if it needed that)."""
        with self._lock:
            if self.paused or not name or name == TOOL_NAME:
                return
            when = when or self._clock()
            learn.bump(self.state["tools"].setdefault(name, learn.fresh_item()), learn.bucket(when.hour), when.date().isoformat())
            self._prune()
            self._save()

    def _prune(self) -> None:
        """Counts only ever grow, so they are capped: the rarest are forgotten first."""
        state = self.state
        if len(state["topics"]) > MAX_TOPICS:
            kept = sorted(state["topics"].items(), key=lambda pair: (-pair[1], pair[0]))[:KEEP_TOPICS]
            state["topics"] = dict(kept)
        if len(state["asks"]) > MAX_ASKS:
            ranked = sorted(state["asks"].items(), key=lambda pair: (-pair[1]["n"], -len(pair[1]["days"]), pair[0]))[:KEEP_ASKS]
            state["asks"] = dict(ranked)
        if len(state["tools"]) > MAX_TOOLS:
            ranked = sorted(state["tools"].items(), key=lambda pair: (-pair[1]["n"], pair[0]))[:MAX_TOOLS]
            state["tools"] = dict(ranked)

    # ------------------------------------------------------------------ using it

    def profile(self) -> list[str]:
        with self._lock:
            return learn.profile_lines(self.state)

    def prompt_block(self) -> str:
        """What goes into his system prompt: nothing while paused, or before he knows enough to say anything."""
        with self._lock:
            lines = [] if self.paused else learn.profile_lines(self.state)
        if not lines:
            return ""
        return "\n".join(["", HEADER, *[f"- {line}" for line in lines]])

    # ------------------------------------------------------------------ what the user can do

    def show(self) -> str:
        with self._lock:
            state = self.state
            out = [f"Learning is {'PAUSED (I am not learning and I am not using it)' if self.paused else 'on'}. "
                   f"I have counted {state['messages']} of your messages since {state['started']} - counts only, never the messages themselves."]
            lines = learn.profile_lines(state)
            if lines:
                out += ["What I have noticed:", *[f"- {line}" for line in lines]]
            else:
                out.append(f"Not enough to say anything yet: I wait for {learn.MIN_MESSAGES} messages before I conclude anything about you.")
            counted = learn.top_topics(state)
            if counted:
                out.append("Words I have counted most: " + ", ".join(f"{word} ({count})" for word, count in counted) + ".")
            out.append(f"It is all in one file you can open or delete: {self.path}. I can pause, resume or forget it if you ask.")
            return "\n".join(out)

    def pause(self) -> str:
        with self._lock:
            self.state["paused"] = True
            self._save()
        return "Paused: I'm not learning from you and I'm not using what I had learned. Say resume to carry on."

    def resume(self) -> str:
        with self._lock:
            self.state["paused"] = False
            self._save()
        return "Learning again, from now on."

    def forget(self) -> str:
        """Wipe everything. Keeps the pause setting, so someone who paused and then forgot isn't surprised."""
        with self._lock:
            seen, paused = self.state["messages"], self.state["paused"]
            self.state = _fresh(self._clock().date().isoformat())
            self.state["paused"] = paused
            self._save()
        return f"Forgotten: everything I had counted from {seen} of your messages is gone." + (" I'm still paused." if paused else " I'll start again from your next message.")

    def preview(self, action: str) -> dict[str, str] | None:
        """What the user is asked to allow, in a few words."""
        name = (action or "").strip().lower()
        if name == "forget":
            return {"habits": "delete everything I've learned", "counted from": f"{self.state['messages']} messages"}
        if name == "pause":
            return {"habits": "stop learning and stop using it"}
        if name == "resume":
            return {"habits": "start learning again"}
        return None
