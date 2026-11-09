"""What he can notice about the user from their own messages.

Pure functions: no files, no clock, no model. Everything here works on counts and short normalised phrases, never on a
whole message, and a message that looks private is not counted at all.
"""

from __future__ import annotations

import re
from typing import Any

MIN_MESSAGES = 8  # he concludes nothing about the user before he has seen this many
MAX_MESSAGE_CHARS = 500  # longer than this is a pasted document, not a habit
MAX_ASK_WORDS = 8
MAX_ASK_CHARS = 60
MAX_DAYS = 30  # days remembered per routine candidate

BUCKETS = ("morning", "afternoon", "evening", "night")

# A message with any of these is dropped whole: not even its time of day is kept.
SENSITIVE = re.compile(
    r"password|passwd|passcode|secret|token|api[ _-]?key|private key|\bpin\b|\bssn\b|credit card|card number|@|://|www\.|\d{4,}",
    re.IGNORECASE,
)
PATHLIKE = re.compile(r"[/\\`]")  # file paths and code say more about a machine than about a habit

WORD = re.compile(r"[a-z][a-z'-]{2,19}")
STOPWORDS = frozenset(
    """a about above after again all also am an and any are aren't as at be because been before being below between both but by can
    can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further get gets give got had
    hadn't has hasn't have haven't having he her here hers him his how i i'd i'll i'm i've if in into is isn't it it's its just know
    let let's like make me more most my myself need no nor not now of off on once one only or other our out over own please really
    same say see shall she should shouldn't show so some such tell than thanks thank that that's the their them then there these they
    think this those through to too under until up us use very want was wasn't way we well were weren't what what's when where which
    while who whom why will with won't would wouldn't yeah yes you you'd you'll you're you've your yours yourself hey hello hi okay
    ok capri babyai again anything something thing things lot much many going gonna wanna maybe sure
    help explain find open check look try put take near today tonight tomorrow yesterday""".split()
)

BRIEF = re.compile(r"\b(shorter|too long|too wordy|be brief|keep it short|keep it brief|less chatty|tl;?dr|shorten|stop rambling)\b", re.IGNORECASE)
DETAIL = re.compile(r"\b(more detail|explain more|elaborate|go deeper|longer answer|in depth|in-depth|tell me more|be more thorough)\b", re.IGNORECASE)


def bucket(hour: int) -> str:
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 21:
        return "evening"
    return "night"


def countable(text: str) -> bool:
    """False for anything empty, pasted, private-looking, or made of paths or code."""
    return bool(text) and len(text) <= MAX_MESSAGE_CHARS and not SENSITIVE.search(text) and not PATHLIKE.search(text)


def topics(text: str) -> set[str]:
    return {word.strip("'-") for word in WORD.findall(text.lower()) if word not in STOPWORDS and word.strip("'-") not in STOPWORDS}


def normalise_ask(text: str) -> str | None:
    """A short, lower-case, punctuation-free version of a request, or None if it isn't short enough to be a repeatable one."""
    words = re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split()
    phrase = " ".join(words)
    if not 2 <= len(words) <= MAX_ASK_WORDS or len(phrase) > MAX_ASK_CHARS:
        return None
    return phrase


def style_signals(text: str) -> tuple[bool, bool]:
    """(asked for shorter replies, asked for more detail)"""
    return bool(BRIEF.search(text)), bool(DETAIL.search(text))


def fresh_item() -> dict[str, Any]:
    return {"n": 0, "buckets": {name: 0 for name in BUCKETS}, "days": []}


def bump(item: dict[str, Any], when_bucket: str, day: str) -> None:
    item["n"] += 1
    item["buckets"][when_bucket] = item["buckets"].get(when_bucket, 0) + 1
    if day not in item["days"]:
        item["days"] = (item["days"] + [day])[-MAX_DAYS:]


def _routine(kind: str, name: str, item: dict[str, Any]) -> tuple[int, str] | None:
    """A line for something done on at least three different days, mostly at the same time of day."""
    days = len(item["days"])
    top, count = max(item["buckets"].items(), key=lambda pair: (pair[1], pair[0]))
    if item["n"] < 3 or days < 3 or count < 0.6 * item["n"]:
        return None
    what = f'asks "{name}"' if kind == "ask" else f"uses {name}"
    return days, f"Routine: {what} most {top}s ({days} days)."


def profile_lines(state: dict[str, Any]) -> list[str]:
    """The short, plain statements he may act on. Empty until he has seen enough to say anything."""
    messages = state["messages"]
    if messages < MIN_MESSAGES:
        return []
    lines: list[str] = []

    brief, detail = state["style"]["brief"], state["style"]["detail"]
    average = state["words"] / messages
    if brief >= 2 and brief >= 2 * detail:
        lines.append(f"Replies: prefers them short (has asked for shorter ones {brief} times).")
    elif detail >= 2 and detail >= 2 * brief:
        lines.append(f"Replies: likes more detail (has asked for it {detail} times).")
    elif average <= 6:
        lines.append(f"Replies: writes short messages (about {round(average)} words), so keep yours compact.")
    elif average >= 30:
        lines.append(f"Replies: writes long messages (about {round(average)} words), so fuller answers are fine.")

    top_topics = sorted(((count, word) for word, count in state["topics"].items() if count >= 3), key=lambda pair: (-pair[0], pair[1]))[:5]
    if top_topics:
        lines.append("Often brings up: " + ", ".join(word for _, word in top_topics) + ".")

    when, count = max(state["buckets"].items(), key=lambda pair: (pair[1], pair[0]))
    if count >= 0.5 * messages:
        lines.append(f"Mostly talks to you in the {when}.")
    if state["weekend"] >= 0.6 * messages:
        lines.append("Mostly talks to you at weekends.")

    routines = [r for r in (_routine("ask", name, item) for name, item in state["asks"].items()) if r]
    routines += [r for r in (_routine("tool", name, item) for name, item in state["tools"].items()) if r]
    lines += [line for _, line in sorted(routines, key=lambda pair: (-pair[0], pair[1]))[:3]]
    return lines


def top_topics(state: dict[str, Any], limit: int = 8) -> list[tuple[str, int]]:
    ranked = sorted(state["topics"].items(), key=lambda pair: (-pair[1], pair[0]))
    return [(word, count) for word, count in ranked if count >= 2][:limit]
