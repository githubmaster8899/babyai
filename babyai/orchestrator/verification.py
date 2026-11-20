"""Result verification before a task is allowed to satisfy its dependants.

A deterministic check always runs.  Model-based verification is opt-in with
``[orchestrator] verify_with_llm = true``; when that model is unavailable the
deterministic result stands, and no task is trusted merely because a worker
said it was done.
"""

from __future__ import annotations

from typing import Any

from babyai.orchestrator.models import Task, TaskResult

BAD_MARKERS = (
    "blocked by the safety gate",
    "the user declined",
    "no usable result",
    "was called with bad arguments",
    "could not be reached",
    "model follow-up failed",
)


class Verifier:
    def __init__(self, llm_client: Any = None, use_llm: bool = False):
        self.llm_client = llm_client
        self.use_llm = use_llm

    def verify(self, task: Task, result: TaskResult) -> tuple[bool, str]:
        if not result.ok:
            return False, result.error or "the worker produced no result"
        text = result.content.strip()
        lowered = text.lower()
        evidence = " ".join([text, *(item.lower() for item in result.artifacts)])
        for marker in BAD_MARKERS:
            if marker in evidence:
                return False, f"the result reports a failure: {marker}"
        if len(text) < 3:
            return False, "the result is empty"

        if self.use_llm and self.llm_client is not None and task.expected_artifact:
            try:
                prompt = (
                    f"Task: {task.goal}\nRequired result: {task.expected_artifact}\n\n"
                    f"Worker result:\n{text[:3000]}\n\n"
                    "Does the worker result satisfy the required result? "
                    "Answer YES or NO, then one short sentence why."
                )
                response = self.llm_client.complete(
                    messages=[{"role": "user", "content": prompt}], tools=None, stream=False
                )
                answer = ""
                if response:
                    answer = str(response.get("choices", [{}])[0].get("message", {}).get("content") or "")
                first = answer.strip().lower().split()[0].strip(".,:;!?") if answer.strip() else ""
                if first in {"no", "nope", "failed", "false"}:
                    return False, answer.strip()[:300] or "the verifier rejected the result"
            except Exception:
                pass  # the deterministic check above still stands
        return True, ""


__all__ = ["Verifier"]
