"""Unit tests for marcus_toolkit.tending, with a fake board."""

import json
from pathlib import Path

import pytest

from marcus_toolkit import tending
from marcus_toolkit.candidates import CLOSE_MARK, POST_MARK

pytestmark = pytest.mark.unit

S = "candidate.hold.the.north.ford"
T0 = "2026-11-06T13:00:00+00:00"


def _entry(subject=S, observers=None, posts=(), spec_extra=None):
    spec = {
        "name": "Hold the north ford",
        "description": "d",
        "completion_criteria": ["c"],
        "because": ["enemy.north"],
        **(spec_extra or {}),
    }
    if observers:
        spec["observers"] = observers
    opinions = [
        {"agent_id": "planner", "what": POST_MARK + json.dumps(spec), "timestamp": T0}
    ]
    for i, (agent, what) in enumerate(posts, start=1):
        opinions.append(
            {
                "agent_id": agent,
                "what": what,
                "timestamp": f"2026-11-06T13:0{i}:00+00:00",
            }
        )
    return {
        "subject": subject,
        "kind": "assessment",
        "agents": ["planner"] + [a for a, _ in posts],
        "opinions": opinions,
    }


class FakeBoard:
    """Answers create_tasks the way Marcus does: with the tasks it made."""

    def __init__(self, refuse=False):
        self.calls = []
        self.refuse = refuse

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "create_tasks":
            if self.refuse:
                return {"success": False, "error": "refused"}
            return {
                "success": True,
                "tasks_created": 1,
                "tasks": [{"id": "task_from_fake_board"}],
            }
        return {"success": True}


def _tend(board, entries, out, **kw):
    return tending.tend_candidates(
        board,
        entries,
        now="2026-11-06T13:05:00+00:00",
        crew=5,
        controller_id="planner",
        out=out,
        handled=set(),
        **kw,
    )


def test_two_supporters_promote_through_create_tasks_with_lineage_and_quotes(tmp_path):
    """The task carries the crew's words and the lineage; the candidate is closed on the board."""
    board = FakeBoard()
    marker = {
        "subject": "enemy.north",
        "kind": "assessment",
        "agents": ["blue-3"],
        "opinions": [
            {
                "agent_id": "blue-3",
                "what": "red massing north of the ford",
                "timestamp": T0,
            }
        ],
    }
    entries = [
        _entry(
            posts=[("blue-1", "my block is 60 px off"), ("blue-2", "I see them too")]
        ),
        marker,
    ]
    actions = _tend(
        board, entries, tmp_path / "c.jsonl", authors={"enemy.north": ["blue-3"]}
    )
    assert actions["promoted"] == [S]
    created = next(a for n, a in board.calls if n == "create_tasks")
    spec = created["options"]["tasks"][0]
    assert set(spec) <= tending.CREATE_TASK_KEYS
    assert (
        'blue-3 on enemy.north: "red massing north of the ford"' in spec["description"]
    )
    assert (
        "Lineage: from enemy.north (blue-3); supported by blue-1, blue-2"
        in spec["description"]
    )
    closes = [
        a
        for n, a in board.calls
        if n == "give_opinion" and a["what"].startswith(CLOSE_MARK)
    ]
    assert closes and closes[0]["subject"] == S and "promoted" in closes[0]["what"]
    event = json.loads((tmp_path / "c.jsonl").read_text().splitlines()[-1])
    assert event["event"] == "promoted" and event["task_ids"] == [
        "task_from_fake_board"
    ]
    assert event["quoted_lines"] == 1


def test_three_declines_evaporate_and_no_declines_keep_it_open(tmp_path):
    """Views decide; seconds do not."""
    views = {
        S: {
            "shown_to": ["a", "b", "c"],
            "declined_by": ["a", "b", "c"],
            "supported_by": [],
            "awaiting_turn": [],
        }
    }
    assert _tend(FakeBoard(), [_entry()], tmp_path / "c.jsonl", views=views)[
        "evaporated"
    ] == [S]
    views = {
        S: {
            "shown_to": ["a"],
            "declined_by": ["a"],
            "supported_by": [],
            "awaiting_turn": [],
        }
    }
    actions = tending.tend_candidates(
        FakeBoard(),
        [_entry()],
        now="2026-11-06T15:00:00+00:00",
        crew=5,
        controller_id="planner",
        out=tmp_path / "d.jsonl",
        handled=set(),
        views=views,
    )
    assert actions["evaporated"] == [] and actions["waiting"] == [S]
    event = json.loads((tmp_path / "c.jsonl").read_text().splitlines()[-1])
    assert event["event"] == "evaporated" and event["declined_by"] == ["a", "b", "c"]


def test_a_refused_creation_is_retried_then_abandoned_never_recorded_as_promoted(
    tmp_path,
):
    """Run 6's defect: the record must never say a task exists that the board refused."""
    board = FakeBoard(refuse=True)
    entries = [_entry(posts=[("blue-1", "yes"), ("blue-2", "yes")])]
    attempts = {}
    out = tmp_path / "c.jsonl"
    for _ in range(3):
        actions = _tend(board, entries, out, attempts=attempts)
    events = [json.loads(line)["event"] for line in out.read_text().splitlines()]
    assert events == [
        "promotion_failed",
        "promotion_failed",
        "promotion_failed",
        "promotion_abandoned",
    ]
    assert actions["abandoned"] == [S] and "promoted" not in events


def test_a_proposal_becomes_a_posted_candidate_and_the_cap_holds(tmp_path):
    """propose.* posts become candidates with the proposer first; at most crew-size open."""
    board = FakeBoard()
    proposal = {
        "subject": "propose.screen.the.ford",
        "kind": "assessment",
        "agents": ["blue-4"],
        "opinions": [
            {"agent_id": "blue-4", "what": "nobody screens the ford", "timestamp": T0}
        ],
    }
    actions = _tend(board, [proposal], tmp_path / "c.jsonl", labels=["war", "planned"])
    assert actions["posted_from_proposals"] == ["candidate.screen.the.ford"]
    posted = next(a for n, a in board.calls if n == "give_opinion")
    spec = json.loads(posted["what"][len(POST_MARK) :])
    assert spec["proposer"] == "blue-4" and spec["labels"] == ["war", "planned"]
    actions = tending.tend_candidates(
        FakeBoard(),
        [proposal],
        now=T0,
        crew=0,
        controller_id="planner",
        out=tmp_path / "d.jsonl",
        handled=set(),
    )
    assert actions["posted_from_proposals"] == []


def test_a_promoted_tasks_status_is_posted_once_per_change(tmp_path):
    """TASK: lines ride the candidate's subject so agents can read what became of it."""
    board = FakeBoard()
    closed = _entry(posts=[("planner", f"{CLOSE_MARK} promoted")])
    tasks = [
        {
            "name": "Hold the north ford",
            "status": "in_progress",
            "assigned_to": "blue-1",
        }
    ]
    actions = _tend(board, [closed], tmp_path / "c.jsonl", tasks=tasks)
    assert actions["task_status"] == [S] and actions["settled_names"] == [
        "Hold the north ford"
    ]
    line = next(a for n, a in board.calls if n == "give_opinion")["what"]
    assert line == "TASK: held by blue-1"
    closed["opinions"].append(
        {"agent_id": "planner", "what": line, "timestamp": "2026-11-06T13:04:00+00:00"}
    )
    board2 = FakeBoard()
    assert (
        _tend(board2, [closed], tmp_path / "d.jsonl", tasks=tasks)["task_status"] == []
    )


def test_post_candidate_records_a_coordinator_posting(tmp_path):
    """A scripted or model coordinator posts through the same door and the record says so."""
    board = FakeBoard()
    subject = tending.post_candidate(
        board,
        "planner",
        {"name": "Screen the ford", "because": ["enemy.north"]},
        tmp_path / "c.jsonl",
        T0,
    )
    assert subject == "candidate.screen.the.ford"
    event = json.loads((tmp_path / "c.jsonl").read_text())
    assert event["event"] == "posted" and event["from"] == "coordinator"
    assert (
        board.calls[0][0] == "give_opinion" and board.calls[0][1]["subject"] == subject
    )


def test_open_at_end_rows_carry_who_answered(tmp_path):
    """Open is terminal in the record; the row says who was shown it and who declined."""
    rows = tending.open_at_end_records(
        [
            {
                "subject": S,
                "name": "n",
                "because": [],
                "supporters": ["a"],
                "objectors": [],
            }
        ],
        {S: {"shown_to": ["a", "b"], "declined_by": ["b"], "awaiting_turn": []}},
        {S: ["c"]},
        T0,
    )
    assert (
        rows[0]["event"] == "open_at_end"
        and rows[0]["supporters"] == ["a"]
        and rows[0]["declined_by"] == ["b"]
        and rows[0]["abstained_by"] == ["c"]
    )
    assert Path(tmp_path).is_dir()


def test_no_evidence_is_neutral_when_asked(tmp_path):
    """Three NO EVIDENCE answers neither drop a card nor count against it; objections do."""
    posts = (
        ("a", "NO EVIDENCE: my blocks have not seen it"),
        ("b", "NO EVIDENCE: not in sight"),
        ("c", "NO EVIDENCE: cannot judge"),
    )
    views = {
        S: {
            "shown_to": ["a", "b", "c"],
            "declined_by": ["a", "b", "c"],
            "supported_by": [],
            "awaiting_turn": ["d", "e"],
        }
    }
    neutral = tending.tend_candidates(
        FakeBoard(),
        [_entry(posts=posts)],
        now="2026-11-06T15:00:00+00:00",
        crew=5,
        controller_id="planner",
        out=tmp_path / "n.jsonl",
        handled=set(),
        views=views,
        no_evidence_neutral=True,
    )
    assert neutral["evaporated"] == [] and neutral["waiting"] == [S]
    counted = tending.tend_candidates(
        FakeBoard(),
        [_entry(posts=posts)],
        now="2026-11-06T15:00:00+00:00",
        crew=5,
        controller_id="planner",
        out=tmp_path / "c.jsonl",
        handled=set(),
        views=views,
    )
    assert counted["evaporated"] == [S]
    # everyone shown, nobody vouches: the card drops even under the neutral rule
    views[S]["awaiting_turn"] = []
    views[S]["shown_to"] = ["a", "b", "c", "d", "e"]
    done = tending.tend_candidates(
        FakeBoard(),
        [_entry(posts=posts)],
        now="2026-11-06T15:00:00+00:00",
        crew=5,
        controller_id="planner",
        out=tmp_path / "e.jsonl",
        handled=set(),
        views=views,
        no_evidence_neutral=True,
    )
    assert done["evaporated"] == [S]
    # objections still decline
    objected = (
        ("a", "OBJECT: wrong ford"),
        ("b", "OBJECT: already held"),
        ("c", "OBJECT: no"),
    )
    views[S]["awaiting_turn"] = ["d", "e"]
    out = tending.tend_candidates(
        FakeBoard(),
        [_entry(posts=objected)],
        now="2026-11-06T15:00:00+00:00",
        crew=5,
        controller_id="planner",
        out=tmp_path / "o.jsonl",
        handled=set(),
        views=views,
        no_evidence_neutral=True,
    )
    assert out["evaporated"] == [S]
