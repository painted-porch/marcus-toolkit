"""Unit tests for marcus_runner.agent."""

import json
from pathlib import Path

import pytest

from marcus_runner import agent

pytestmark = pytest.mark.unit

ASSIGNED = {
    "success": True,
    "task": {"id": "t1", "name": "Hold the north ford"},
    "lease_epoch": 7,
}
RETRY = {"success": False, "retry_after_seconds": 1, "drain": {"tasks_claimable": 0}}
DRAINED = {"success": False, "status": "BOARD_DRAINED", "reason": "complete"}


class FakeClient:
    """Answers from a script and records every call."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        nxt = self.script.get(name)
        if isinstance(nxt, list):
            return nxt.pop(0) if len(nxt) > 1 else nxt[0]
        return nxt if nxt is not None else {"success": True}


def test_outcomes_follow_the_minecraft_classification():
    """Assigned, retry, drained, ok, failed, transport_error."""
    assert agent.outcome_of("request_next_task", ASSIGNED) == "assigned"
    assert agent.outcome_of("request_next_task", RETRY) == "retry"
    assert agent.outcome_of("request_next_task", DRAINED) == "drained"
    assert agent.outcome_of("report_task_progress", {"success": True}) == "ok"
    assert agent.outcome_of("report_task_progress", {"success": False}) == "failed"
    assert (
        agent.outcome_of("x", {"success": False, "transport_error": True})
        == "transport_error"
    )


def test_registration_carries_the_project_and_reads_the_objective():
    """Marcus hands work only to a registered agent; the primer is read once."""
    client = FakeClient(
        {
            "register_agent": {"success": True},
            "query_project_history": {
                "success": True,
                "data": [
                    {"subject": "objective", "opinions": [{"what": "Hold the line."}]}
                ],
            },
        }
    )
    a = agent.BoardAgent(
        client, "blue-1", "p-blue", role="commander", skills=["command"]
    )
    a.register()
    assert client.calls[0] == (
        "register_agent",
        {
            "agent_id": "blue-1",
            "name": "blue-1",
            "role": "commander",
            "skills": ["command"],
            "project_id": "p-blue",
        },
    )
    assert a.objective == "Hold the line."


def test_pull_absorbs_retries_then_holds_the_task_and_epoch(tmp_path):
    """Each ask is one logged call; the hold persists to disk."""
    client = FakeClient(
        {
            "register_agent": {"success": True},
            "request_next_task": [RETRY, RETRY, ASSIGNED],
            "query_project_history": {"success": True, "data": []},
            "get_task_context": {"success": True},
        }
    )
    log = []
    sleeps = []
    hold = tmp_path / "hold.json"
    a = agent.BoardAgent(
        client, "blue-1", "p", log=log.append, hold_path=hold, sleep=sleeps.append
    )
    r = a.pull(absorb=True)
    assert r["status"] == "assigned"
    assert r["lease_epoch"] == 7
    assert a.task == {"id": "t1", "name": "Hold the north ford"}
    assert sleeps == [1.0, 1.0]
    assert json.loads(hold.read_text()) == {"task": a.task, "lease_epoch": 7}
    pulls = [x["outcome"] for x in log if x["tool"] == "request_next_task"]
    assert pulls == ["retry", "retry", "assigned"]
    for k in ("at", "agent", "tool", "args", "outcome", "ms", "reply"):
        assert k in log[0]


def test_pull_without_absorb_returns_the_retry_and_a_drain_ends_it():
    """A turn-based world asks once per turn."""
    client = FakeClient(
        {
            "register_agent": {"success": True},
            "request_next_task": [RETRY],
            "query_project_history": {"success": True, "data": []},
        }
    )
    assert agent.BoardAgent(client, "a", "p").pull()["status"] == "retry"
    client2 = FakeClient(
        {
            "register_agent": {"success": True},
            "request_next_task": [DRAINED],
            "query_project_history": {"success": True, "data": []},
        }
    )
    assert agent.BoardAgent(client2, "a", "p").pull()["status"] == "drained"


def test_report_carries_epoch_and_evidence_and_a_close_clears_the_hold(tmp_path):
    """A restarted agent reads its hold back; a completed report clears it."""
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"task": {"id": "t1", "name": "n"}, "lease_epoch": 7}))
    client = FakeClient({"report_task_progress": {"success": True}})
    a = agent.BoardAgent(client, "blue-1", "p", hold_path=hold)
    assert a.task == {"id": "t1", "name": "n"}
    a.report("in_progress", "moving", progress=30, subject="ford.north")
    assert client.calls[0][1]["lease_epoch"] == 7
    assert client.calls[0][1]["subject"] == "ford.north"
    assert a.task is not None
    a.report("completed", "held", evidence={"check": "no_enemy_within", "ok": True})
    assert client.calls[1][1]["evidence"] == {"check": "no_enemy_within", "ok": True}
    assert client.calls[1][1]["progress"] == 100
    assert a.task is None
    assert not hold.exists()


def test_decide_and_opinion_shapes():
    """A decision reads 'what because why.'; an opinion about the task names it."""
    client = FakeClient(
        {"log_decision": {"success": True}, "give_opinion": {"success": True}}
    )
    a = agent.BoardAgent(client, "blue-1", "p")
    a.decide("withdrew block 3", "losses passed 15%", subject="ford.north")
    assert client.calls[0][1] == {
        "agent_id": "blue-1",
        "decision": "DECISION: withdrew block 3 because losses passed 15%.",
        "task_id": "",
        "subject": "ford.north",
    }
    a.task = {"id": "t1", "name": "n"}
    a.opinion("enemy.north", "red density rising", "umpire", about_task=True)
    assert client.calls[1][1]["task_id"] == "t1"


def test_a_missing_reply_is_a_transport_error_not_a_crash(tmp_path):
    """The shell never raises on the wire; the record says what happened."""
    client = FakeClient({"register_agent": None})
    client.script = {"register_agent": None}
    client.call_tool = lambda name, arguments=None: None
    log = []
    a = agent.BoardAgent(client, "a", "p", log=log.append)
    assert a.pull()["status"] == "error"
    assert log[0]["outcome"] == "transport_error"
    assert agent.jsonl_logger(tmp_path / "x" / "calls.jsonl")({"a": 1}) is None
    assert Path(tmp_path / "x" / "calls.jsonl").read_text() == '{"a": 1}\n'
