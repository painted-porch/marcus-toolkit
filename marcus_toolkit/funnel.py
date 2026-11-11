"""The candidate funnel, read from ``candidates.jsonl``, never reconstructed."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .views import candidate_views


def candidate_funnel(
    path: Path | None,
    calls: Iterable[dict[str, Any]] | None = None,
    planned: Path | None = None,
) -> dict[str, Any]:
    """
    Summarise the candidate funnel from the controller's ``candidates.jsonl``.

    Parameters
    ----------
    path : Optional[Path]
        ``candidates.jsonl``; missing means candidates were off.
    calls : Optional[Iterable[Dict[str, Any]]]
        The agents' board calls, for the view log; with it, evaporations split
        into ``evaporated_declined`` and ``evaporated_unseen``.
    planned : Optional[Path]
        A planner's ``planned.jsonl`` whose passes record ``candidates_posted``.

    Returns
    -------
    Dict[str, Any]
        Counts, per-candidate view numbers, the promotions, and the raw events.
    """
    empty: dict[str, Any] = {
        "promoted": 0,
        "evaporated": 0,
        "open_at_end": 0,
        "shown_to": {},
        "declined_by": {},
        "awaiting_turn": {},
        "abstained_by": {},
        "abstentions": 0,
        "promotions": [],
        "evaporated_declined": 0,
        "evaporated_unseen": 0,
        "posted_by_planner": 0,
        "posted_from_proposals": 0,
        "posted_from_observations": 0,
        "posted_by_coordinator": 0,
        "objections": 0,
        "mean_seconds_to_promotion": None,
        "supporters": {},
        "views": {},
        "events": [],
    }
    by_planner: set[str] = set()
    if planned is not None and planned.exists():
        for line in planned.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for subject in record.get("candidates_posted") or []:
                by_planner.add(str(subject))
    if path is None or not path.exists():
        return {**empty, "posted_by_planner": len(by_planner)}
    events: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    promoted = [e for e in events if e.get("event") == "promoted"]
    evaporated = [e for e in events if e.get("event") == "evaporated"]
    open_at_end = [e for e in events if e.get("event") == "open_at_end"]
    posted_all = [e for e in events if e.get("event") == "posted"]
    observed = [e for e in posted_all if e.get("observers")]
    coordinator = [e for e in posted_all if e.get("from") == "coordinator"]
    proposals = [
        e
        for e in posted_all
        if not e.get("observers") and e.get("from") != "coordinator"
    ]
    supporters: Counter[str] = Counter()
    for e in promoted:
        for agent in e.get("supporters") or []:
            supporters[agent] += 1
    times = [
        int(e["seconds_to_promotion"])
        for e in promoted
        if e.get("seconds_to_promotion") is not None
    ]
    closed_at = {
        str(e.get("subject")): str(e.get("at"))
        for e in events
        if e.get("event") in ("promoted", "evaporated")
        and e.get("subject")
        and e.get("at")
    }
    seen_by = (
        candidate_views(list(calls), before=closed_at) if calls is not None else {}
    )
    subjects = {str(e.get("subject")) for e in events if e.get("subject")} | by_planner
    views = {s: len(seen_by.get(s, [])) for s in sorted(subjects)}
    declined = sum(1 for e in evaporated if views.get(str(e.get("subject")), 0) > 0)
    closed = promoted + evaporated + open_at_end

    def per(key: str) -> dict[str, int]:
        return {
            str(e.get("subject")): len(e.get(key) or [])
            for e in closed
            if e.get("subject")
        }

    return {
        "promoted": len(promoted),
        "evaporated": len(evaporated),
        "open_at_end": len(open_at_end),
        "shown_to": per("shown_to"),
        "declined_by": per("declined_by"),
        "awaiting_turn": per("awaiting_turn"),
        "abstained_by": per("abstained_by"),
        "abstentions": sum(
            len(e.get("agents") or []) for e in events if e.get("event") == "abstained"
        ),
        "promotions": [
            {
                "name": e.get("name"),
                "subject": e.get("subject"),
                "supporters": list(e.get("supporters") or []),
                "objectors": list(e.get("objectors") or []),
                "seconds_to_promotion": e.get("seconds_to_promotion"),
                "shown_to": len(e.get("shown_to") or []),
                "declined_by": len(e.get("declined_by") or []),
                "abstained_by": len(e.get("abstained_by") or []),
                "task_ids": list(e.get("task_ids") or []),
            }
            for e in promoted
        ],
        "evaporated_declined": declined,
        "evaporated_unseen": len(evaporated) - declined,
        "views": views,
        "posted_by_planner": len(by_planner),
        "posted_from_proposals": len(proposals),
        "posted_from_observations": len(observed),
        "posted_by_coordinator": len(coordinator),
        "objections": sum(1 for e in promoted + evaporated if e.get("objectors")),
        "mean_seconds_to_promotion": round(sum(times) / len(times)) if times else None,
        "supporters": dict(sorted(supporters.items())),
        "events": events,
    }
