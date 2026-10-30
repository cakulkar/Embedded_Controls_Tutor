"""Synchronous publish/subscribe bus so departments/services stay decoupled."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)
Handler = Callable[..., Any]

# Known topics (documented contract; new phases add theirs here).
ATTEMPT_GRADED = "attempt.graded"
LESSON_COMPLETED = "lesson.completed"
HINT_REVEALED = "hint.revealed"
SOLUTION_REVEALED = "solution.revealed"
LEARNER_CREATED = "learner.created"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        self._subs[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._subs[topic]:
                self._subs[topic].remove(handler)

        return unsubscribe

    def publish(self, topic: str, **payload: Any) -> list[Exception]:
        """Deliver to every subscriber; one failing handler never blocks others."""
        errors: list[Exception] = []
        for handler in list(self._subs.get(topic, [])):
            try:
                handler(**payload)
            except Exception as exc:  # noqa: BLE001 - isolate subscribers
                log.exception("event handler failed for %s", topic)
                errors.append(exc)
        return errors
