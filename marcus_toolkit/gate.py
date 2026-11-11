"""
Does the agreement chain still work? Asked of a run's record, in three links.

Work is agreed before it is created: the coordinator posts a candidate; agents
answer it; two supporters promote it into a task an agent then picks up. Three
links, each needing the one before it: a candidate is posted at all; support
is recorded against a candidate; a promotion creates a task on the board. The
verdict is one of four, and PARTIAL is a real outcome rather than a softened
failure: PROVEN, PARTIAL (posted and supported, nobody promoted, an agent
choice), FAILED (a link that does not depend on an agent's choice did not fire,
or a promotion created nothing), NOT PROVEN (nothing ran).

Lifted from the Minecraft plumbing's ``live_check_no_regression.py``, the
reading half; starting a run is the extension's business.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .measure import read_jsonl

PROVEN, FAILED, NOT_PROVEN, PARTIAL = 0, 1, 2, 3
NAMES = {
    PROVEN: "PROVEN",
    FAILED: "FAILED",
    NOT_PROVEN: "NOT PROVEN",
    PARTIAL: "PARTIAL",
}


def task_exists(kanban_db: Path, task_id: str) -> bool | None:
    """Return whether a task with this id is on the board, or ``None`` if unreadable."""
    if not kanban_db.exists():
        return None
    con = sqlite3.connect(f"file:{kanban_db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select count(*) from tasks where id = ?", (task_id,)
        ).fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return None
    finally:
        con.close()


def check_chain(
    candidates_jsonl: Path, kanban_db: Path, planned_jsonl: Path | None = None
) -> dict[str, Any]:
    """
    Report which links of the chain fired.

    Parameters
    ----------
    candidates_jsonl : Path
        The controller's event stream.
    kanban_db : Path
        The run's own board, to confirm each promoted task id.
    planned_jsonl : Optional[Path]
        A planner's passes, whose ``candidates_posted`` count as postings.

    Returns
    -------
    Dict[str, Any]
        ``code`` (0 proven, 3 partial, 1 failed, 2 not proven), ``verdict``,
        the three link counts, and ``lines`` explaining the reading.
    """
    events = read_jsonl(candidates_jsonl)
    passes = read_jsonl(planned_jsonl) if planned_jsonl else []
    by_planner: set[str] = set()
    for record in passes:
        for subject in record.get("candidates_posted") or []:
            by_planner.add(str(subject))
    # A candidate that closed, failed or stayed open was necessarily posted, so
    # link 1 counts every subject any candidate event names, not only the
    # "posted" events a planning pass may record elsewhere.
    candidate_events = {
        "posted",
        "promoted",
        "evaporated",
        "open_at_end",
        "promotion_failed",
        "promotion_abandoned",
        "abstained",
        "already_on_board",
    }
    posted = {
        str(e.get("subject"))
        for e in events
        if e.get("event") in candidate_events and e.get("subject")
    } | by_planner
    promoted = [e for e in events if e.get("event") == "promoted"]
    failed = [e for e in events if e.get("event") == "promotion_failed"]
    supported = [e for e in events if (e.get("supporters") or [])]
    lines: list[str] = [
        f"LINK 1, candidates posted: {len(posted)}",
        f"LINK 2, candidates with support recorded: {len(supported)}",
        f"LINK 3, promotions: {len(promoted)} ({len(failed)} refused by the board)",
    ]
    out: dict[str, Any] = {
        "posted": len(posted),
        "supported": len(supported),
        "promoted": len(promoted),
        "refused": len(failed),
        "lines": lines,
    }

    def finish(code: int, reason: str) -> dict[str, Any]:
        lines.append(f"VERDICT: {NAMES[code]} -- {reason}")
        return {**out, "code": code, "verdict": NAMES[code]}

    if not events and not passes:
        return finish(NOT_PROVEN, "nothing was tended, so no link was exercised.")
    if not posted:
        return finish(
            FAILED,
            "no candidate was posted; posting does not depend on an agent's choice.",
        )
    for event in promoted:
        ids = [str(i) for i in (event.get("task_ids") or [])]
        if not ids:
            return finish(FAILED, "a promotion created nothing.")
        for task_id in ids:
            if task_exists(kanban_db, task_id) is not True:
                return finish(
                    FAILED,
                    f"promoted task {task_id[:12]} is not on the run's own board.",
                )
        lines.append(f"  promoted {str(event.get('name'))[:60]}: task_ids {ids}")
    if failed and not promoted:
        return finish(FAILED, "every promotion was refused by the board.")
    if not promoted:
        if not supported:
            return finish(
                PARTIAL,
                "candidates posted and no agent answered any; link 2 unexercised.",
            )
        return finish(
            PARTIAL,
            "support was recorded and no candidate reached promotion;"
            " link 3 unexercised.",
        )
    return finish(
        PROVEN,
        "candidates posted, support recorded, and a promotion created a task"
        " on the board.",
    )
