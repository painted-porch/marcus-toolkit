"""Unit tests for marcus_toolkit.views: a decline needs an opportunity."""

import pytest

from marcus_toolkit import views

pytestmark = pytest.mark.unit

S = "candidate.hold.the.north.ford"


def _view(agent, at, *subjects):
    return {
        "at": at,
        "agent": agent,
        "tool": "candidate_view",
        "args": {"subjects": list(subjects or [S])},
    }


def _turn(agent, at, tool="report_task_progress", subject=None, what="working"):
    args = {"agent_id": agent, "what": what}
    if subject:
        args["subject"] = subject
    return {"at": at, "agent": agent, "tool": tool, "args": args}


def _poll(agent, at):
    return {"at": at, "agent": agent, "tool": "request_next_task", "args": {}}


def test_the_four_cases():
    """Supported; declined after a turn; shown but no turn; shown twice is one agent."""
    assert views.tally([_view("a", "1"), _turn("a", "2", subject=S)])[S][
        "supported_by"
    ] == ["a"]
    assert views.tally([_view("a", "1"), _turn("a", "2")])[S]["declined_by"] == ["a"]
    row = views.tally([_view("a", "1")])[S]
    assert row["declined_by"] == [] and row["awaiting_turn"] == ["a"]
    row = views.tally([_view("a", "1"), _view("a", "2"), _turn("a", "3")])[S]
    assert row["declined_by"] == ["a"] and row["shown_to"] == ["a"]


def test_what_counts_as_a_turn():
    """An absorbed poll is not a turn; an opinion elsewhere is; order and agent matter."""
    assert views.tally([_view("a", "1"), _poll("a", "2")])[S]["awaiting_turn"] == ["a"]
    assert views.tally(
        [_view("a", "1"), _turn("a", "2", tool="give_opinion", subject="x")]
    )[S]["declined_by"] == ["a"]
    assert views.tally([_turn("a", "1"), _view("a", "2")])[S]["declined_by"] == []
    row = views.tally([_view("a", "1"), _view("b", "1"), _turn("a", "2")])[S]
    assert row["declined_by"] == ["a"] and row["awaiting_turn"] == ["b"]


def test_an_objection_or_abstention_is_not_support_and_a_decision_is_a_turn():
    """OBJECT: and NO EVIDENCE: on the subject do not support; a decision is a turn, never support."""
    row = views.tally([_view("a", "1"), _turn("a", "2", subject=S, what="OBJECT: no")])[
        S
    ]
    assert row["supported_by"] == [] and row["declined_by"] == ["a"]
    calls = [
        _view("a", "1"),
        {"at": "2", "agent": "a", "tool": "log_decision", "args": {"subject": S}},
    ]
    row = views.tally(calls)[S]
    assert row["supported_by"] == [] and row["declined_by"] == ["a"]


def test_candidate_views_counts_only_views_before_the_close():
    """A view of a stale marker after the close is not a view that could have declined it."""
    calls = [
        _view("a", "2026-11-06T11:54:00+00:00", "candidate.wall"),
        _view("b", "2026-11-06T11:59:40+00:00", "candidate.wall", "candidate.nobody"),
    ]
    closed = {
        "candidate.wall": "2026-11-06T11:55:05+00:00",
        "candidate.nobody": "2026-11-06T11:52:00+00:00",
    }
    assert views.candidate_views(calls, before=closed) == {"candidate.wall": ["a"]}
    assert views.candidate_views(calls) == {
        "candidate.nobody": ["b"],
        "candidate.wall": ["a", "b"],
    }
