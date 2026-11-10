"""He thinks for himself about how to get better, asks the user as soon as he has a good idea, and starts on it when they say yes.

What he watches, as plain counts (never anything the user said, and nothing from a tool's output): tools he reached for
and doesn't have, tools that crashed, how many messages he could only chat because his model can't use tools, tool calls
he wrote out as text instead of making, and - at most once a day, by a read-only ``git fetch`` - whether newer commits
exist. When a count passes a threshold it becomes an *idea*: a short title and a reason that code builds out of those
counts. He can also file an idea he thought of himself (``suggest``); his own words are kept for the user to read, but
they are never put into his system prompt, so nothing he read on a web page can reach his instructions through here.

What he does with an idea: he asks as soon as he can - in the reply to the very message where it formed, or in the next one -
at most one idea per message, and (after a no, or when nobody answered) not again for ``min_hours`` (a day by default). The
moment the user says yes (``accept``) he begins implementing it himself, without being asked again: for a code idea the change
is started on a branch for him, and a note in his instructions keeps him at it until it is applied or thrown away. Starting
is as far as a yes goes by itself: nothing goes live without the user approving the test run and the apply, and every other
``upgrade_`` step (update, model switch, notes) still asks. A dismissed idea never comes back; an idea nobody answered is raised
at most three times, a week apart. An idea can only be accepted once it has been put to the user.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from babyai.habits import learn
from babyai.upgrade.gitops import UpgradeError

KINDS = ("code", "model", "version", "notes")
OPEN, ACCEPTED, DISMISSED, PARKED, SUPERSEDED = "open", "accepted", "dismissed", "parked", "superseded"
STATUSES = (OPEN, ACCEPTED, DISMISSED, PARKED, SUPERSEDED)
FAMILIES = ("update", "model", "missing", "crash")  # the order signal ideas are raised in; his own ideas ("self") are never raised

MIN_HOURS = 24.0  # between one idea being put to the user and the next
RERAISE_DAYS = 7  # an idea nobody answered comes back after this long...
MAX_SHOWINGS = 3  # ...at most this many times in all, then it is parked
QUIET_DAYS = 7  # after a no, nothing of the same family for this long
CHECK_EVERY_HOURS = 24  # between looks for a newer version
MAX_IDEAS = 40
MAX_OPEN = 8
MAX_NAMES = 60
MAX_SELF_PER_DAY = 2
MISSING_AT, CRASHES_AT, PLAIN_TURNS_AT, TEXT_CALLS_AT = 2, 3, 5, 3  # how many times before it is worth an idea
TITLE_MAX, WHY_MAX = 100, 200

NAME = re.compile(r"[a-z0-9_.\-]{1,40}")
BRANCH = re.compile(r"[A-Za-z0-9._/\-]{1,60}")
SHA = re.compile(r"[0-9a-f]{7,40}")

NEXT_STEP = {
    "code": "start a change with upgrade_code start (give it a goal), write the change and a test for it with write or replace, then test, diff and apply",
    "model": "run upgrade_model list now, pick one that fits, then pull it and switch to it (the user approves each of those)",
    "version": "run upgrade_version check now, then update (the new version's tests run first), then restart (the user approves those)",
    "notes": "write it down with upgrade_notes add_note or save_skill (the user approves)",
}

NEXT_STEP_BEGUN = "look at how similar code is written first (read_file, find_files or search_files in the branch folder), then write the change and a test for it (upgrade_code write or replace), then test, diff and apply"  # the branch is already started

HINT = (
    "Something you have been thinking about for yourself, an idea for getting better (idea #{id}): {title}. {why} "
    "Tell the user about it in this reply, ONCE, briefly and in your own voice: first deal with what they asked, then add the "
    "idea and ask if they would like you to do it. If they don't answer, don't repeat it. It is only a suggestion: nothing "
    "changes until they say yes. Don't start on it yourself before they say yes. When they say yes, call upgrade_ideas accept "
    "{id} and then carry on implementing it yourself without asking again (they still approve the test run and the apply, so "
    "nothing goes live without them); if they say no, call upgrade_ideas dismiss {id}. The rules always win."
)

CARRY_ON = (
    "The user said yes to your idea #{id}{title}, and you are implementing it on a branch (upgrade_code status shows where you "
    "are and what it is for). Deal with what they have just said first; then carry on without being asked again: write the code "
    "and a test for it, then run the tests, look at the diff and apply it. The user is asked to approve the test run and the "
    "apply, and nothing goes live without them. Don't start another change until this one is applied or thrown away; if they "
    "tell you to stop, upgrade_code discard throws it away. The rules always win."
)

NOT_STARTED = (
    "The user said yes to your idea #{id}{title}, but it has not been started yet (another change was in the way). If nothing is "
    "in your way now, start it: call upgrade_ideas accept {id}. The rules always win."
)

MAX_START_TRIES = 3  # a yes that could not start is reminded about this many times, then left to the user to ask again


def _fresh() -> dict[str, Any]:
    return {
        "version": 1, "paused": False, "next_id": 1, "ideas": [], "missing": {}, "crashes": {}, "plain_turns": 0, "text_calls": 0,
        "last_shown": None, "last_shown_id": None, "checked": None, "quiet": {}, "self_today": ["", 0],
    }


def _valid(state: Any) -> bool:
    """Is this the shape this file always has? Anything else (hand-edited, damaged, from another version) is dropped."""
    try:
        return (
            state["version"] == 1 and isinstance(state["paused"], bool) and isinstance(state["next_id"], int)
            and all(isinstance(state[key], int) and state[key] >= 0 for key in ("plain_turns", "text_calls"))
            and all(state[key] is None or isinstance(state[key], str) for key in ("last_shown", "checked"))
            and (state.get("last_shown_id") is None or isinstance(state["last_shown_id"], int))
            and all(isinstance(k, str) and isinstance(v, int) for table in (state["missing"], state["crashes"]) for k, v in table.items())
            and all(isinstance(k, str) and isinstance(v, str) for k, v in state["quiet"].items())
            and isinstance(state["self_today"][0], str) and isinstance(state["self_today"][1], int)
            and all(
                isinstance(i["id"], int) and isinstance(i["key"], str) and i["kind"] in KINDS and isinstance(i["title"], str)
                and isinstance(i["why"], str) and i["source"] in ("signal", "self") and i["status"] in STATUSES
                and isinstance(i["created"], str) and isinstance(i["shown"], list) and all(isinstance(t, str) for t in i["shown"])
                and isinstance(i.get("told", False), bool) and isinstance(i.get("waiting", False), bool)
                and (i.get("branch") is None or isinstance(i["branch"], str)) and isinstance(i.get("tries", 0), int)
                for i in state["ideas"]
            )
        )
    except (KeyError, TypeError, AttributeError, IndexError):
        return False


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class Ideas:
    def __init__(
        self, directory: str | Path, clock: Callable[[], datetime] = datetime.now, busy: Callable[[], bool] | None = None,
        min_hours: float = MIN_HOURS, fetch_updates: bool = True, branch: Callable[[], str | None] | None = None,
    ) -> None:
        self.directory = Path(os.path.expanduser(str(directory)))
        self.path = self.directory / "ideas.json"
        self._clock = clock
        self._busy = busy
        self._branch = branch  # the branch of the change he has in progress, if any
        try:
            self.min_hours = max(1.0, float(min_hours))  # never more often than hourly, whatever the config says
        except (TypeError, ValueError):
            self.min_hours = MIN_HOURS
        self.fetch_updates = bool(fetch_updates)
        self._lock = threading.RLock()
        self.current: int | None = None  # the idea in front of him for this message, if any
        self.state = self._load()

    # ------------------------------------------------------------------ the file

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = None
        return state if _valid(state) else _fresh()

    def _save(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
            os.replace(temp, self.path)
        except OSError:
            pass  # noticing things must never get in the way of an answer

    @staticmethod
    def _when(text: str | None) -> datetime | None:
        try:
            return datetime.fromisoformat(text) if text else None
        except ValueError:
            return None

    def _stamp(self, now: datetime) -> str:
        return now.isoformat(timespec="seconds")

    # ------------------------------------------------------------------ noticing (counts only)

    @property
    def paused(self) -> bool:
        return self.state["paused"]

    def _count(self, table: str, name: str) -> None:
        counts = self.state[table]
        counts[name] = counts.get(name, 0) + 1
        if len(counts) > MAX_NAMES:
            self.state[table] = dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:MAX_NAMES])

    def _noticed(self) -> None:
        self._derive(self._clock())
        self._save()

    def missing_tool(self, name: str) -> None:
        """He asked for a tool he doesn't have."""
        name = (name or "").strip().lower()
        with self._lock:
            if self.paused or not NAME.fullmatch(name):
                return
            self._count("missing", name)
            self._noticed()

    def tool_crashed(self, name: str) -> None:
        """A tool he has raised an error instead of answering. (A tool saying 'no' is not a crash.)"""
        name = (name or "").strip().lower()
        with self._lock:
            if self.paused or not NAME.fullmatch(name) or name.startswith("upgrade_"):
                return
            self._count("crashes", name)
            self._noticed()

    def plain_turn(self) -> None:
        """A message he could only chat about, because his model can't use tools."""
        with self._lock:
            if self.paused:
                return
            self.state["plain_turns"] += 1
            self._noticed()

    def text_call(self) -> None:
        """A tool call his model wrote out as text instead of making."""
        with self._lock:
            if self.paused:
                return
            self.state["text_calls"] += 1
            self._noticed()

    def update_seen(self, upstream: str, tip: str, ahead: int, behind: int) -> None:
        """What a look at the upstream branch found."""
        with self._lock:
            if self.paused:
                return
            now = self._clock()
            if behind and not ahead and SHA.fullmatch(tip or ""):
                branch = upstream if BRANCH.fullmatch(upstream or "") else "my upstream branch"
                self._add(
                    f"update:{tip[:8]}", "version", f"Update to the newest version ({_plural(int(behind), 'new commit')})",
                    f"There {'is' if int(behind) == 1 else 'are'} {_plural(int(behind), 'new commit')} on {branch} that I haven't pulled.", "signal", now,
                )
            elif not behind:  # nothing newer any more: an old "update" idea is out of date
                for idea in self.state["ideas"]:
                    if idea["key"].startswith("update:") and idea["status"] == OPEN:
                        idea["status"] = SUPERSEDED
            self._save()

    def _derive(self, now: datetime) -> None:
        """Turn counts that have passed a threshold into ideas. The words are made here, from the counts."""
        state = self.state
        for name, count in sorted(state["missing"].items()):
            if count >= MISSING_AT:
                self._add(f"missing:{name}", "code", f"Build a {name} tool", f"I reached for a tool called {name} {_plural(count, 'time')} and I don't have one.", "signal", now)
        for name, count in sorted(state["crashes"].items()):
            if count >= CRASHES_AT:
                self._add(f"crash:{name}", "code", f"Fix my {name} tool", f"It crashed {_plural(count, 'time')} when I used it.", "signal", now)
        if state["plain_turns"] >= PLAIN_TURNS_AT:
            self._add("model:tools", "model", "Move to a model that can use tools", f"The model I run on can't use tools, so for {_plural(state['plain_turns'], 'message')} I could only talk.", "signal", now)
        if state["text_calls"] >= TEXT_CALLS_AT:
            self._add("model:calls", "model", "Try a model that makes tool calls properly", f"{_plural(state['text_calls'], 'time')} I wrote a tool call out as text instead of making it.", "signal", now)

    def _add(self, key: str, kind: str, title: str, why: str, source: str, now: datetime) -> dict[str, Any] | None:
        """A new idea, unless he has had it before (in any state), was told no recently, or has too many already."""
        state = self.state
        family = key.split(":", 1)[0]
        if any(idea["key"] == key for idea in state["ideas"]):
            return None
        quiet_until = self._when(state["quiet"].get(family))
        if quiet_until and now < quiet_until:
            return None
        if sum(1 for idea in state["ideas"] if idea["status"] == OPEN) >= MAX_OPEN:
            return None
        if len(state["ideas"]) >= MAX_IDEAS:
            closed = [idea for idea in state["ideas"] if idea["status"] != OPEN]
            if not closed:
                return None
            state["ideas"].remove(min(closed, key=lambda idea: idea["id"]))
        if family == "update":  # a newer version makes an older "update" idea out of date
            for idea in state["ideas"]:
                if idea["key"].startswith("update:") and idea["status"] == OPEN:
                    idea["status"] = SUPERSEDED
        idea = {
            "id": state["next_id"], "key": key, "kind": kind, "title": title, "why": why, "source": source, "status": OPEN,
            "created": self._stamp(now), "shown": [],
        }
        state["next_id"] += 1
        state["ideas"].append(idea)
        return idea

    # ------------------------------------------------------------------ looking for a newer version

    def check_due(self) -> bool:
        with self._lock:
            if self.paused or not self.fetch_updates:
                return False
            last = self._when(self.state["checked"])
            return last is None or self._clock() - last >= timedelta(hours=CHECK_EVERY_HOURS)

    def check_updates(self, version: Any) -> None:
        """Look - a fetch, nothing else - whether there are newer commits. Meant for a background thread; it never raises."""
        with self._lock:
            if not self.check_due():
                return
            self.state["checked"] = self._stamp(self._clock())  # first, so a slow or failing fetch isn't retried in a loop
            self._save()
        try:
            found = version.quick_state()
            self.update_seen(str(found["upstream"]), str(found["tip"]), int(found["ahead"]), int(found["behind"]))
        except Exception:
            pass  # not a checkout, no network, no upstream: nothing to suggest

    # ------------------------------------------------------------------ putting one in front of him

    def begin_turn(self) -> None:
        """Pick the idea (at most one) he may mention during this message. Nothing is recorded until ``mark_shown``."""
        with self._lock:
            self.current = None
            if self.paused:
                return
            try:
                if self._busy is not None and self._busy():  # he is in the middle of a change: no new ideas on top of it
                    return
            except Exception:
                return
            now = self._clock()
            last = self._when(self.state["last_shown"])
            last_idea = self._find(self.state.get("last_shown_id"))
            said_yes = last_idea is not None and last_idea["status"] == ACCEPTED  # then there is nothing to wait out
            if last is not None and not said_yes and now - last < timedelta(hours=self.min_hours):
                return
            ready = [idea for idea in self.state["ideas"] if idea["source"] == "signal" and idea["status"] == OPEN and self._due(idea, now)]
            if not ready:
                return

            def rank(idea: dict[str, Any]) -> tuple[int, int]:
                family = idea["key"].split(":", 1)[0]
                return (FAMILIES.index(family) if family in FAMILIES else len(FAMILIES), idea["id"])

            self.current = min(ready, key=rank)["id"]

    def _due(self, idea: dict[str, Any], now: datetime) -> bool:
        if not idea["shown"]:
            return True
        last = self._when(idea["shown"][-1])
        return last is not None and now - last >= timedelta(days=RERAISE_DAYS)

    def prompt_block(self) -> str:
        """What goes into his system prompt this message: a reminder to carry on with a change the user said yes to, and an idea if
        one is due. Only words made from counts get in: never his own words from an idea he filed."""
        with self._lock:
            return self._carry_on() + self._fresh_hint()

    def _fresh_hint(self) -> str:
        idea = self._find(self.current) if self.current is not None else None
        if idea is None or self.paused or idea["source"] != "signal" or idea["status"] != OPEN:
            return ""
        return "\n" + HINT.format(id=idea["id"], title=idea["title"], why=idea["why"])

    def _carry_on(self) -> str:
        """While he is implementing an idea the user said yes to, a note to keep going (not a licence to do more: the test run and the
        apply still ask)."""
        try:
            branch = self._branch() if self._branch is not None else None
        except Exception:
            return ""
        if self.paused:
            return ""
        if branch:
            idea = next((i for i in self.state["ideas"] if i["status"] == ACCEPTED and i.get("branch") == branch), None)
            template = CARRY_ON
        else:  # nothing in progress: is there a yes that never got to start?
            idea = next((i for i in self.state["ideas"] if i["status"] == ACCEPTED and i.get("waiting") and i.get("tries", 0) < MAX_START_TRIES), None)
            template = NOT_STARTED
        if idea is None:
            return ""
        title = f" ({idea['title']})" if idea["source"] == "signal" else ""  # his own words stay out of his instructions
        return "\n" + template.format(id=idea["id"], title=title)

    def nudge(self) -> str:
        """An idea that formed during this very message can be raised in his next reply instead of waiting for the next message.
        Returns the words for him (empty if nothing is due). It counts as raised only when ``mark_shown`` is called, once the model
        has them - so a reply that never came does not use the idea up."""
        with self._lock:
            self.begin_turn()
            return self._fresh_hint().strip()

    def mark_shown(self) -> None:
        """The message went through, so the idea was in front of him. It counts as raised, and the clock for the next one starts."""
        with self._lock:
            idea = self._find(self.current) if self.current is not None else None
            self.current = None
            if idea is None or idea["status"] != OPEN:
                return
            now = self._clock()
            idea["shown"].append(self._stamp(now))
            idea["told"] = True
            self.state["last_shown"] = self._stamp(now)
            self.state["last_shown_id"] = idea["id"]
            if len(idea["shown"]) >= MAX_SHOWINGS:
                idea["status"] = PARKED
            self._save()

    def _find(self, idea_id: Any) -> dict[str, Any] | None:
        for idea in self.state["ideas"]:
            if idea["id"] == idea_id:
                return idea
        return None

    # ------------------------------------------------------------------ what he and the user can do

    def _number(self, idea_id: Any) -> dict[str, Any]:
        try:
            number = int(str(idea_id).strip().lstrip("#"))
        except ValueError:
            raise UpgradeError(f"{idea_id!r} isn't an idea number. upgrade_ideas list shows them.") from None
        idea = self._find(number)
        if idea is None:
            raise UpgradeError(f"I have no idea number {number}. upgrade_ideas list shows them.")
        return idea

    def show(self) -> str:
        with self._lock:
            ideas = self.state["ideas"]
            out = []
            if self.paused:
                out.append("My suggestions are PAUSED: I'm not noticing things and I'm not putting ideas to you. Say resume to carry on.")
            live = [idea for idea in ideas if idea["status"] in (OPEN, PARKED)]
            if not ideas:
                out.append("I don't have any ideas for getting better right now. I watch for tools I keep reaching for and don't have, tools that crash, "
                           "a model that can't use tools, and newer versions of me; if I think of something I'll say so.")
            else:
                out.append("My ideas for getting better. Say yes to one and I'll start on it; you approve the test run and the apply, so nothing goes live without you.")
                for idea in live + [idea for idea in ideas if idea["status"] == ACCEPTED]:
                    out.append(f"#{idea['id']} ({idea['status']}) {idea['title']} - {idea['why']}")
                    if idea["status"] in (OPEN, PARKED) and not idea.get("told"):
                        idea["told"] = True  # the user is being shown it, so it has been put to them
                        self._save()
                dropped = sum(1 for idea in ideas if idea["status"] in (DISMISSED, SUPERSEDED))
                if dropped:
                    out.append(f"({_plural(dropped, 'idea')} you said no to or that went out of date, not shown.)")
            out.append(f"It is all in one file you can open or delete: {self.path}. I can pause or resume it if you ask.")
            return "\n".join(out)

    def accept(self, idea_id: Any, begin: Callable[[dict[str, Any]], str] | None = None) -> str:
        """The user said yes to an idea, and he begins implementing it now. ``begin`` (given by the tool) does the first step of a code
        idea - starting the change on a branch. Nothing goes live from here: the test run and the apply still ask the user, and
        so does every other upgrade_ step. An idea that was never put to the user cannot be accepted: there is no yes to act on."""
        with self._lock:
            idea = self._number(idea_id)
            if idea["status"] == SUPERSEDED:
                raise UpgradeError(f"Idea #{idea['id']} is out of date (something newer replaced it), so there's nothing to do for it.")
            if not idea.get("told"):
                raise UpgradeError(f"I haven't put idea #{idea['id']} to the user yet, so there is no yes to act on. Tell them about it and ask first.")
            idea["status"] = ACCEPTED
            started = ""
            if begin is not None and not idea.get("branch"):  # not begun yet (a yes that was blocked can be started later)
                try:
                    started = begin(idea)
                except UpgradeError as exc:
                    idea["waiting"] = True
                    idea["tries"] = idea.get("tries", 0) + 1
                    self._save()
                    return f"Idea #{idea['id']} is a yes. I couldn't start it just yet: {exc} It stays accepted, and I'll start it as soon as that is sorted out."
                idea["waiting"] = False
            self._save()
            lead = f"Idea #{idea['id']} ({idea['title']}) is a yes, so begin now and don't ask again."
            if started:
                lead += f" {started}"
            return f"{lead} Next: {NEXT_STEP_BEGUN if started else NEXT_STEP[idea['kind']]}. The user is asked to approve the test run and the apply; nothing goes live without them."

    def began(self, idea_id: Any, branch: str) -> None:
        """Remember which branch an accepted idea is being implemented on, so the note to carry on shows while it is in progress."""
        with self._lock:
            idea = self._find(idea_id)
            if idea is not None and isinstance(branch, str):
                idea["branch"] = branch
                self._save()

    def dismiss(self, idea_id: Any) -> str:
        with self._lock:
            idea = self._number(idea_id)
            idea["status"] = DISMISSED
            family = idea["key"].split(":", 1)[0]
            self.state["quiet"][family] = self._stamp(self._clock() + timedelta(days=QUIET_DAYS))
            self._save()
            return f"Dropped idea #{idea['id']}. I won't bring it up again, and I'll hold back on ideas like it for a week."

    def suggest(self, kind: str, title: str, why: str) -> str:
        """An idea he thought of himself. It is kept for the user to read; it is never put into his instructions."""
        kind = (kind or "").strip().lower()
        title, why = " ".join((title or "").split()), " ".join((why or "").split())
        with self._lock:
            if self.paused:
                return "My suggestions are paused, so I'm keeping that to myself."
            if kind not in KINDS:
                raise UpgradeError(f"kind must be one of: {', '.join(KINDS)}.")
            if not title or len(title) > TITLE_MAX or len(why) > WHY_MAX:
                raise UpgradeError(f"Give a short title (up to {TITLE_MAX} characters) and a reason (up to {WHY_MAX}).")
            if not learn.countable(f"{title} {why}"):
                raise UpgradeError("Keep it general: no passwords, links, long numbers, file paths or code in an idea.")
            now = self._clock()
            today = now.date().isoformat()
            day, used = self.state["self_today"]
            if day == today and used >= MAX_SELF_PER_DAY:
                return "I've already shared enough ideas today; this one can wait."
            quiet_until = self._when(self.state["quiet"].get("self"))
            if quiet_until and now < quiet_until:
                return "The user said no to an idea of mine recently, so I'm keeping this one to myself for now."
            key = "self:" + " ".join(re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split())[:60]
            idea = self._add(key, kind, title, why, "self", now)
            if idea is None:
                same = next((i for i in self.state["ideas"] if i["key"] == key), None)
                return f"I already have that idea (#{same['id']})." if same else "I can't keep another idea right now: there are too many open ones."
            self.state["self_today"] = [today, (used if day == today else 0) + 1]
            idea["shown"].append(self._stamp(now))  # he is about to tell the user, so this counts as raised
            idea["told"] = True
            self.state["last_shown"] = self._stamp(now)
            self.state["last_shown_id"] = idea["id"]
            self._save()
            return (
                f"Noted as idea #{idea['id']}. Tell the user about it now, in this reply: a sentence or two in your own voice, then ask if they'd like you to do it. "
                f"Nothing changes until they say yes. When they do, call upgrade_ideas accept {idea['id']} and carry on implementing it yourself (they still approve the test run and the apply)."
            )

    def pause(self) -> str:
        with self._lock:
            self.state["paused"] = True
            self.current = None
            self._save()
        return "Paused: I'm not noticing things to improve and I won't put ideas to you. Say resume to carry on."

    def resume(self) -> str:
        with self._lock:
            self.state["paused"] = False
            self._save()
        return "Thinking about how to get better again, from now on."

    def preview(self, action: str) -> dict[str, str] | None:
        """What the user is asked to allow, in a few words."""
        name = (action or "").strip().lower()
        if name == "pause":
            return {"suggestions": "stop noticing things to improve and stop putting ideas to you"}
        if name == "resume":
            return {"suggestions": "start noticing and suggesting again"}
        return None
