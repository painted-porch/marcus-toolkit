"""
Who was shown a candidate, and who declined it.

Evaporation counts views rather than seconds (Larry, 2026-11-06, "views,
k=3"), and a view alone is not a decline: a decline requires an opportunity.
An agent counts against a candidate only when, after the reply that showed
it, that agent had at least one further turn in which support was possible
and did not give it. The four cases:

* shown, then supported: support, never a decline;
* shown, then a turn without naming the subject: one decline;
* shown, then no further turn (died, or the run ended): not a decline;
* shown twice, then one turn: still one decline; agents are counted, not views.

The data: the agent's runtime writes a ``candidate_view`` record into its
``board-calls.jsonl`` for every reply that carried candidates, beside the one
record per board call. Views are never reported to the board, where they
could be mistaken for support.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .candidates import ABSTAIN_MARK, OBJECT_MARK

#: Calls only a deciding agent makes; an absorbed poll is not a turn.
OPPORTUNITY_TOOLS: set[str] = {
    "report_task_progress",
    "give_opinion",
    "log_artifact",
    "get_task_context",
    "query_project_history",
    "log_decision",
}
#: Calls through which support can be given.
SUPPORT_TOOLS: set[str] = {"report_task_progress", "give_opinion"}
NOT_SUPPORT_MARKS = (OBJECT_MARK, ABSTAIN_MARK)


def _is_support(what: Any) -> bool:
    said = str(what or "").strip().upper()
    return not any(said.startswith(mark) for mark in NOT_SUPPORT_MARKS)


def tally(
    calls: Iterable[dict[str, Any]], subjects: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    """
    Compute, per candidate subject, who was shown it and who declined it.

    Parameters
    ----------
    calls : Iterable[Dict[str, Any]]
        Board-call records in time order (``marcus_toolkit.measure.read_calls``).
    subjects : Optional[Iterable[str]]
        Only these subjects; ``None`` tallies every subject seen.

    Returns
    -------
    Dict[str, Dict[str, Any]]
        ``{subject: {"shown_to", "declined_by", "supported_by", "awaiting_turn"}}``,
        each a sorted list of agents.
    """
    wanted = set(subjects) if subjects is not None else None
    first_view: dict[str, dict[str, str]] = {}
    supported: dict[str, set[str]] = {}
    turns: dict[str, list[str]] = {}

    for call in calls:
        agent = str(call.get("agent") or "")
        tool = str(call.get("tool") or "")
        at = str(call.get("at") or "")
        args = call.get("args") or {}
        if not agent or not at:
            continue
        if tool in OPPORTUNITY_TOOLS:
            turns.setdefault(agent, []).append(at)
        if tool == "candidate_view":
            for subject in args.get("subjects") or []:
                subject = str(subject)
                if wanted is not None and subject not in wanted:
                    continue
                first_view.setdefault(subject, {}).setdefault(agent, at)
        elif tool in SUPPORT_TOOLS:
            subject = str(args.get("subject") or "")
            if (
                subject
                and (wanted is None or subject in wanted)
                and _is_support(args.get("what"))
            ):
                supported.setdefault(subject, set()).add(agent)

    out: dict[str, dict[str, Any]] = {}
    for subject in sorted(set(first_view) | set(wanted or ())):
        seen = first_view.get(subject, {})
        supporters = supported.get(subject, set())
        declined: set[str] = set()
        awaiting: set[str] = set()
        for agent, view_at in seen.items():
            if agent in supporters:
                continue
            if any(t > view_at for t in turns.get(agent, [])):
                declined.add(agent)
            else:
                awaiting.add(agent)
        out[subject] = {
            "shown_to": sorted(seen),
            "declined_by": sorted(declined),
            "supported_by": sorted(supporters & set(seen)),
            "awaiting_turn": sorted(awaiting),
        }
    return out


def candidate_views(
    calls: Iterable[dict[str, Any]], before: dict[str, str] | None = None
) -> dict[str, list[str]]:
    """
    Count, per candidate subject, the agents that were shown it while it was open.

    Parameters
    ----------
    calls : Iterable[Dict[str, Any]]
        Board-call records.
    before : Optional[Dict[str, str]]
        Subject to its close time; a view at or after the close is a view of
        a stale marker and does not count. A subject absent here is open.

    Returns
    -------
    Dict[str, List[str]]
        Subject to the sorted agents that saw it in time; subjects nobody saw
        in time are absent.
    """
    seen: dict[str, set[str]] = {}
    for call in calls:
        if call.get("tool") != "candidate_view":
            continue
        agent = str(call.get("agent") or "")
        at = str(call.get("at") or "")
        for subject in (call.get("args") or {}).get("subjects") or []:
            subject = str(subject)
            closed = (before or {}).get(subject)
            if closed and at >= closed:
                continue
            if agent:
                seen.setdefault(subject, set()).add(agent)
    return {s: sorted(a) for s, a in seen.items() if a}
