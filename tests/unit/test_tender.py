"""Unit tests for marcus_toolkit.tender with a fake board and a scratch state directory."""

import json
import sqlite3

import pytest

from marcus_toolkit.candidates import POST_MARK
from marcus_toolkit.tender import Tender, board_tasks

pytestmark = pytest.mark.unit


class FakeBoard:
    def __init__(self):
        self.calls = []
        self.opinions = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "query_project_history":
            return {"success": True, "data": self.opinions}
        if name == "create_tasks":
            return {"success": True, "tasks_created": 1, "tasks": [{"id": "t-new"}]}
        return {"success": True}


def test_scripted_posting_respects_the_cap_and_records_each(tmp_path):
    """A script posts through the same door as a model coordinator, up to crew size."""
    board = FakeBoard()
    t = Tender(board, "p", "planner", crew=2, out=tmp_path / "c.jsonl")
    posted = t.post_scripted(
        [
            {"name": "A", "because": []},
            {"name": "B", "because": []},
            {"name": "C", "because": []},
        ]
    )
    assert posted == ["candidate.a", "candidate.b"]
    rows = [
        json.loads(line) for line in (tmp_path / "c.jsonl").read_text().splitlines()
    ]
    assert [r["event"] for r in rows] == ["posted", "posted"] and rows[0][
        "from"
    ] == "coordinator"


def test_a_pass_reads_the_board_the_views_and_the_tasks(tmp_path):
    """Two supporters on a posted candidate promote it; the task names come from the board file."""
    board = FakeBoard()
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table tasks (id text, name text, status text, assigned_to text, project_id text)"
    )
    con.execute("insert into tasks values ('1', 'Seeded', 'todo', null, 'p')")
    con.commit()
    con.close()
    assert board_tasks(db, "p") == [
        {"id": "1", "name": "Seeded", "status": "todo", "assigned_to": None}
    ]
    assert board_tasks(tmp_path / "missing.db", "p") == []
    spec = {
        "name": "Hold the ford",
        "description": "d",
        "completion_criteria": ["c"],
        "because": ["enemy.north"],
    }
    board.opinions = [
        {
            "subject": "candidate.hold.the.ford",
            "agents": ["planner", "a", "b"],
            "opinions": [
                {
                    "agent_id": "planner",
                    "what": POST_MARK + json.dumps(spec),
                    "timestamp": "2026-11-11T10:00:00+00:00",
                },
                {
                    "agent_id": "a",
                    "what": "yes",
                    "timestamp": "2026-11-11T10:00:05+00:00",
                },
                {
                    "agent_id": "b",
                    "what": "yes",
                    "timestamp": "2026-11-11T10:00:06+00:00",
                },
            ],
        },
        {
            "subject": "enemy.north",
            "agents": ["c"],
            "opinions": [
                {
                    "agent_id": "c",
                    "what": "massing",
                    "timestamp": "2026-11-11T09:59:00+00:00",
                }
            ],
        },
    ]
    state = tmp_path / "state" / "a"
    state.mkdir(parents=True)
    (state / "board-calls.jsonl").write_text(
        json.dumps(
            {
                "at": "2026-11-11T10:00:01+00:00",
                "agent": "a",
                "tool": "candidate_view",
                "args": {"subjects": ["candidate.hold.the.ford"]},
            }
        )
        + "\n"
    )
    t = Tender(
        board,
        "p",
        "planner",
        crew=5,
        out=tmp_path / "c.jsonl",
        state_dir=tmp_path / "state",
        kanban_db=db,
    )
    actions = t.run_pass()
    assert actions["promoted"] == ["candidate.hold.the.ford"]
    created = next(a for n, a in board.calls if n == "create_tasks")["options"][
        "tasks"
    ][0]
    assert "Lineage: from enemy.north (c); supported by a, b" in created["description"]
    assert t.passes == 1 and t.flush_open() == 0


def test_load_build_seed_imports_module_function(tmp_path, monkeypatch):
    """``module:function`` on the path is imported; a bad spec is refused."""
    import sys

    from marcus_toolkit.tender import load_build_seed

    (tmp_path / "rule_mod.py").write_text(
        "def rule(entry):\n    return None if entry.get('subject') == 'x' else {'name': 'n'}\n"
    )
    sys.path.insert(0, str(tmp_path))
    rule = load_build_seed("rule_mod:rule")
    assert rule({"subject": "x"}) is None
    assert rule({"subject": "ford"})["name"] == "n"
    import pytest

    with pytest.raises(ValueError):
        load_build_seed("nocolon")
