"""Unit tests for marcus_toolkit.funnel and marcus_toolkit.gate."""

import json
import sqlite3

import pytest

from marcus_toolkit.funnel import candidate_funnel
from marcus_toolkit.gate import FAILED, NOT_PROVEN, PARTIAL, PROVEN, check_chain

pytestmark = pytest.mark.unit


def _write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def test_funnel_counts_times_and_supporters(tmp_path):
    """Promoted, evaporated, postings by origin, mean time, supporters, objections."""
    p = tmp_path / "candidates.jsonl"
    _write(
        p,
        [
            {
                "at": "t",
                "event": "posted",
                "subject": "candidate.a",
                "name": "a",
                "from": "propose.a",
                "proposer": "x",
            },
            {
                "at": "t",
                "event": "posted",
                "subject": "candidate.b",
                "name": "b",
                "from": "coordinator",
            },
            {
                "at": "t",
                "event": "promoted",
                "subject": "candidate.b",
                "name": "b",
                "supporters": ["x", "y"],
                "objectors": [],
                "seconds_to_promotion": 300,
                "task_ids": ["k1"],
            },
            {
                "at": "t",
                "event": "evaporated",
                "subject": "candidate.c",
                "name": "c",
                "supporters": [],
                "objectors": ["z"],
                "age_seconds": 900,
            },
            {
                "at": "t",
                "event": "promoted",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x", "w"],
                "objectors": [],
                "seconds_to_promotion": 120,
                "task_ids": ["k2"],
            },
        ],
    )
    f = candidate_funnel(p)
    assert f["promoted"] == 2 and f["evaporated"] == 1
    assert f["posted_from_proposals"] == 1 and f["posted_by_coordinator"] == 1
    assert f["mean_seconds_to_promotion"] == 210
    assert f["supporters"] == {"w": 1, "x": 2, "y": 1}
    assert f["objections"] == 1
    assert candidate_funnel(tmp_path / "none.jsonl")["promoted"] == 0


def test_funnel_splits_evaporations_by_views_before_the_close(tmp_path):
    """A view after the close is a view of a stale marker."""
    p = tmp_path / "candidates.jsonl"
    _write(
        p,
        [
            {
                "at": "2026-11-06T11:55:05+00:00",
                "event": "evaporated",
                "subject": "candidate.wall",
                "name": "wall",
                "supporters": [],
                "objectors": [],
                "age_seconds": 272,
            },
            {
                "at": "2026-11-06T11:52:00+00:00",
                "event": "evaporated",
                "subject": "candidate.nobody",
                "name": "nobody",
                "supporters": [],
                "objectors": [],
                "age_seconds": 272,
            },
        ],
    )
    calls = [
        {
            "at": "2026-11-06T11:54:00+00:00",
            "agent": "a",
            "tool": "candidate_view",
            "args": {"subjects": ["candidate.wall"]},
        },
        {
            "at": "2026-11-06T11:59:40+00:00",
            "agent": "b",
            "tool": "candidate_view",
            "args": {"subjects": ["candidate.wall", "candidate.nobody"]},
        },
    ]
    f = candidate_funnel(p, calls=calls)
    assert f["evaporated_declined"] == 1 and f["evaporated_unseen"] == 1
    assert f["views"] == {"candidate.nobody": 0, "candidate.wall": 1}


def _board(tmp_path, ids):
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (id text, name text)")
    con.executemany("insert into tasks values (?, ?)", [(i, i) for i in ids])
    con.commit()
    con.close()
    return db


def test_the_gate_reads_each_link(tmp_path):
    """PROVEN needs a promotion whose task is on the board; the other three verdicts name their reason."""
    c = tmp_path / "candidates.jsonl"
    db = _board(tmp_path, ["t1"])
    assert check_chain(c, db)["code"] == NOT_PROVEN
    _write(
        c,
        [
            {
                "at": "t",
                "event": "posted",
                "subject": "candidate.a",
                "name": "a",
                "from": "coordinator",
            }
        ],
    )
    assert check_chain(c, db)["code"] == PARTIAL
    _write(
        c,
        [
            {
                "at": "t",
                "event": "posted",
                "subject": "candidate.a",
                "name": "a",
                "from": "coordinator",
            },
            {
                "at": "t",
                "event": "open_at_end",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x"],
            },
        ],
    )
    assert check_chain(c, db)["code"] == PARTIAL
    _write(
        c,
        [
            {
                "at": "t",
                "event": "promoted",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x", "y"],
                "task_ids": ["t1"],
            }
        ],
    )
    r = check_chain(c, db)
    assert r["code"] == PROVEN and r["verdict"] == "PROVEN"
    _write(
        c,
        [
            {
                "at": "t",
                "event": "promoted",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x", "y"],
                "task_ids": [],
            }
        ],
    )
    assert check_chain(c, db)["code"] == FAILED
    _write(
        c,
        [
            {
                "at": "t",
                "event": "promoted",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x", "y"],
                "task_ids": ["missing"],
            }
        ],
    )
    assert check_chain(c, db)["code"] == FAILED
    _write(
        c,
        [
            {
                "at": "t",
                "event": "promotion_failed",
                "subject": "candidate.a",
                "name": "a",
                "supporters": ["x", "y"],
            }
        ],
    )
    assert check_chain(c, db)["code"] == FAILED
