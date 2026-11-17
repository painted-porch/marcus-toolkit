"""Unit tests for marcus_toolkit.candidates: the rules, lifted from Minecraft."""

import json
from typing import Any

import pytest

from marcus_toolkit.candidates import (
    ABSTAIN_AGAINST,
    CANDIDATE_PREFIX,
    CLOSE_MARK,
    EVAPORATION_SECONDS,
    MARGIN,
    POST_MARK,
    THRESHOLD,
    VETO_SECONDS,
    Candidate,
    can_post,
    candidate_from_observation,
    candidate_from_proposal,
    candidate_subject,
    decide,
    lineage_line,
    parse_candidate,
    parse_task_line,
    post_text,
    task_line,
    task_state,
)

pytestmark = pytest.mark.unit

T0 = "2026-11-06T04:00:00+00:00"
T1 = "2026-11-06T04:01:00+00:00"
T2 = "2026-11-06T04:02:00+00:00"
SPEC = {
    "name": "Hold the north ford",
    "description": "Red is massing north.",
    "completion_criteria": ["no red unit within 120 px of the ford at turn 12"],
    "labels": ["war", "planned"],
    "priority": "medium",
    "because": ["enemy.north"],
}


def _entry(subject: str, opinions: list[dict[str, Any]]) -> dict[str, Any]:
    agents = sorted({o["agent_id"] for o in opinions})
    return {
        "subject": subject,
        "kind": "assessment",
        "agents": agents,
        "opinions": opinions,
    }


def _op(agent: str, what: str, ts: str) -> dict[str, Any]:
    return {"agent_id": agent, "what": what, "timestamp": ts}


def _cand(
    supporters, objectors, posted_at=T0, observers=None, abstainers=None
) -> Candidate:
    return Candidate(
        subject="candidate.x",
        spec={**SPEC, "name": "x"},
        posted_at=posted_at,
        supporters=supporters,
        objectors=objectors,
        observers=observers or [],
        abstainers=abstainers or [],
    )


def test_larrys_settings():
    """Two supporters, the superseded 270 s, a veto window shorter than it."""
    assert THRESHOLD == 2
    assert EVAPORATION_SECONDS == 270
    assert 0 < VETO_SECONDS < EVAPORATION_SECONDS
    assert CANDIDATE_PREFIX == "candidate."


def test_subject_and_text():
    """A slug under the prefix; the spec rides as JSON after the mark."""
    assert candidate_subject("Hold the north ford") == "candidate.hold.the.north.ford"
    text = post_text(SPEC)
    assert text.startswith(POST_MARK)
    assert json.loads(text[len(POST_MARK) :])["name"] == SPEC["name"]


def test_the_controller_defines_a_candidate_and_never_counts_as_support():
    """Support is the subject on an agent's own post; objection begins OBJECT:."""
    entry = _entry(
        "candidate.hold.the.north.ford",
        [
            _op("blue-2", "OBJECT: the ford is already held", T2),
            _op("blue-1", "my block is 60 px from it", T1),
            _op("planner", post_text(SPEC), T0),
        ],
    )
    c = parse_candidate(entry, controller_ids={"planner"})
    assert c is not None
    assert c.posted_at == T0
    assert c.supporters == ["blue-1"]
    assert c.objectors == ["blue-2"]
    assert c.answered_at == {"blue-1": T1, "blue-2": T2}


def test_an_agent_counts_once_and_its_latest_word_wins():
    """Support then OBJECT: is an objection; NO EVIDENCE: is an abstention."""
    entry = _entry(
        "candidate.x",
        [
            _op("blue-1", "OBJECT: no", T2),
            _op("blue-1", "yes", T1),
            _op("blue-3", "NO EVIDENCE: my blocks are south", T1),
            _op("planner", post_text({**SPEC, "name": "x"}), T0),
        ],
    )
    c = parse_candidate(entry, {"planner"})
    assert (
        c.supporters == [] and c.objectors == ["blue-1"] and c.abstainers == ["blue-3"]
    )


def test_what_is_not_a_candidate():
    """No controller posting, a foreign subject, or a closed candidate."""
    assert (
        parse_candidate(_entry("candidate.orphan", [_op("a", "?", T1)]), {"planner"})
        is None
    )
    assert (
        parse_candidate(_entry("enemy.north", [_op("a", "massing", T1)]), {"planner"})
        is None
    )
    closed = _entry(
        "candidate.x",
        [
            _op("planner", post_text({**SPEC, "name": "x"}), T0),
            _op("planner", f"{CLOSE_MARK} promoted", T1),
        ],
    )
    assert parse_candidate(closed, {"planner"}) is None
    not_closed = _entry(
        "candidate.x",
        [
            _op("planner", post_text({**SPEC, "name": "x"}), T0),
            _op("blue-1", f"{CLOSE_MARK} evaporated", T1),
        ],
    )
    assert (
        parse_candidate(not_closed, {"planner"}) is not None
    ), "an agent cannot close one"


def test_decide_promotes_on_two_with_a_majority_and_never_on_a_clock():
    """Two against one carries; two against two does not; silence evaporates, never promotes."""
    assert decide(_cand(["a", "b"], []), now=T1) == "promote"
    assert decide(_cand(["a", "b"], ["c"]), now=T1) == "promote"
    assert decide(_cand(["a", "b"], ["c", "d"]), now=T1) == "wait"
    assert decide(_cand(["a"], []), now=T1) == "wait"
    assert decide(_cand([], []), now="2026-11-06T05:00:00+00:00") == "evaporate"


def test_declines_decide_evaporation_when_given():
    """Views, k=3: three declines end it; no declines keep it open for an hour."""
    assert decide(_cand([], []), now=T1, declined_by=3) == "evaporate"
    assert decide(_cand([], []), now=T1, declined_by=2) == "wait"
    assert (
        decide(_cand([], []), now="2026-11-06T06:00:00+00:00", declined_by=0) == "wait"
    )
    assert (
        decide(_cand(["a", "b"], ["c", "d"]), now=T1, declined_by=0, awaiting=0)
        == "evaporate"
    )


def test_observers_wait_the_veto_window():
    """Born with two observers, it promotes after the window unless someone objects."""
    c = _cand(["a", "b"], [], observers=["a", "b"])
    assert decide(c, now="2026-11-06T04:00:30+00:00") == "wait"
    assert decide(c, now="2026-11-06T04:02:01+00:00") == "promote"
    assert decide(c, now="2026-11-06T04:02:01+00:00", declined_by=5) == "promote"


def test_the_settling_window_runs_from_support_completion():
    """An objection inside the window blocks; after it the candidate promotes."""
    c = _cand(["a", "b"], [])
    c.answered_at = {"a": T1, "b": T2}
    assert decide(c, now="2026-11-06T04:02:10+00:00", settling_seconds=30) == "wait"
    assert decide(c, now="2026-11-06T04:02:40+00:00", settling_seconds=30) == "promote"


def test_the_cap_and_the_proposal_rule():
    """At most crew-size open; a propose.* post becomes a candidate with its author first."""
    assert can_post(4, 5) and not can_post(5, 5)
    entry = _entry(
        "propose.screen.the.ford", [_op("blue-4", "nobody screens the ford; I can", T1)]
    )
    spec, proposer = candidate_from_proposal(entry, labels=["war", "planned"])
    assert spec["name"] == "screen the ford"
    assert spec["because"] == ["propose.screen.the.ford"]
    assert spec["labels"] == ["war", "planned"]
    assert spec["proposed_at"] == T1
    assert proposer == "blue-4"


def test_an_observation_becomes_a_candidate_with_the_extensions_seed_rule():
    """The toolkit has no domain; the extension supplies what an observation becomes."""
    entry = _entry(
        "enemy.north",
        [_op("blue-1", "red massing north", T0), _op("blue-2", "I see it too", T1)],
    )
    spec = candidate_from_observation(
        entry,
        lambda e: {"name": f"Answer {e['subject']}", "description": "d"},
        labels=["war", "planned"],
    )
    assert spec["name"] == "Answer enemy.north"
    assert spec["observers"] == ["blue-1", "blue-2"]
    assert spec["because"] == ["enemy.north"]
    assert spec["labels"] == ["war", "planned"]
    assert spec["completion_criteria"]


def test_lineage_and_task_lines():
    """The paragraph a promoted task carries, and the controller's status lines."""
    line = lineage_line(SPEC, ["blue-1", "blue-3"], {"enemy.north": ["blue-2"]})
    assert line == "Lineage: from enemy.north (blue-2); supported by blue-1, blue-3"
    for word in ("plan", "document", "check", "verify", "test", "resolve"):
        assert word not in line.lower()
    assert task_line("held", "blue-1") == "TASK: held by blue-1"
    assert parse_task_line("TASK: held by blue-1") == ("held", "blue-1")
    assert parse_task_line("TASK: blocked") == ("blocked", None)
    assert parse_task_line("TASK: whatever") is None
    assert task_state({"status": "in_progress", "assigned_to": "blue-1"}) == (
        "held",
        "blue-1",
    )
    assert task_state({"status": "done"}) == ("done", None)
    assert task_state({"status": "todo"}) == ("open", None)


def test_candidate_from_observation_returns_none_when_the_rule_has_no_card():
    """An extension's rule may decline a subject; the toolkit passes that through."""
    from marcus_toolkit.candidates import candidate_from_observation

    entry = {"subject": "morale.low", "agents": ["a", "b"], "opinions": []}
    assert candidate_from_observation(entry, lambda _e: None) is None
    spec = candidate_from_observation(entry, lambda _e: {"name": "Hold it"})
    assert spec is not None and spec["observers"] == ["a", "b"]


def test_the_default_margin_is_one_and_an_abstention_is_not_contrary():
    """The shipped rule is unchanged: supporters need only outnumber objectors,
    and a NO EVIDENCE: answer carries no weight in the promotion arithmetic."""
    assert decide(_cand(["a", "b"], [], abstainers=["c", "d"]), now=T1) == "promote"
    assert decide(_cand(["a", "b"], ["c"], abstainers=["d"]), now=T1) == "promote"
    assert MARGIN == 1
    assert ABSTAIN_AGAINST is False


def test_a_wider_margin_asks_supporters_to_clear_contrary_evidence_by_more():
    """At margin 2 a two-against-one candidate no longer carries."""
    assert decide(_cand(["a", "b"], []), now=T1, margin=2) == "promote"
    assert decide(_cand(["a", "b"], ["c"]), now=T1, margin=2) == "wait"
    assert decide(_cand(["a", "b", "c"], ["d"]), now=T1, margin=2) == "promote"


def test_counting_an_abstention_as_contrary_blocks_a_candidate_nobody_can_vouch_for():
    """Two supporters and two abstentions is the shape a planted report produced."""
    votes = _cand(["a", "b"], [], abstainers=["c", "d"])
    assert decide(votes, now=T1) == "promote"
    assert decide(votes, now=T1, abstain_against=True) == "wait"
    assert decide(_cand(["a", "b"], [], abstainers=["c"]), now=T1, abstain_against=True) == "promote"


def test_policy_e_is_the_two_knobs_together():
    """Margin 2 with abstentions counted: two supporters and one abstention fails."""
    assert decide(_cand(["a", "b"], [], abstainers=["c"]), now=T1, margin=2, abstain_against=True) == "wait"
    assert decide(_cand(["a", "b"], [], abstainers=[]), now=T1, margin=2, abstain_against=True) == "promote"


def test_the_margin_applies_to_an_observer_born_candidate_too():
    """A card grown from corroboration clears the same bar, after its veto window."""
    after = "2026-11-06T04:05:00+00:00"
    c = _cand(["a", "b"], ["c"], observers=["a", "b"])
    assert decide(c, now=after) == "promote"
    assert decide(c, now=after, margin=2) == "evaporate"


def test_a_stricter_rule_never_promotes_what_the_default_refused():
    """The property the offline replay in the war study depends on."""
    shapes = [
        (["a", "b"], [], []),
        (["a", "b"], ["c"], []),
        (["a", "b"], [], ["c"]),
        (["a", "b"], ["c", "d"], []),
        (["a"], [], []),
        (["a", "b", "c"], ["d"], ["e"]),
    ]
    for sup, obj, abst in shapes:
        c = _cand(sup, obj, abstainers=abst)
        if decide(c, now=T1, margin=2, abstain_against=True) == "promote":
            assert decide(c, now=T1) == "promote", (sup, obj, abst)


def test_a_margin_below_one_is_refused_so_silence_can_never_put_work_on_the_board():
    """A margin of zero would promote a tie; the rule forbids configuring that."""
    with pytest.raises(ValueError):
        decide(_cand(["a", "b"], ["c", "d"]), now=T1, margin=0)
