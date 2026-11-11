"""
The candidate lifecycle: agents ratify, the controller promotes.

Decided by Larry on 2026-11-06 for the Minecraft experiment
(``minecraft-spike/DESIGN-ratified-growth.md``) and lifted here unchanged in
its rules. A coordinator does not grow the task graph by itself. What it would
have added as a task is posted as a *candidate*: a marker under the reserved
subject prefix ``candidate.`` whose text carries the task spec, visible to
every agent like any other opinion. An agent supports a candidate by posting
the same subject on its own report or opinion, with its evidence; it objects
with a text beginning ``OBJECT:``; it abstains with ``NO EVIDENCE:``. A
candidate is promoted to a task when two distinct agents support it and
supporters outnumber objectors; it evaporates once three distinct agents have
been shown it and declined it. At most crew-size candidates are open at once.
The controller's own posting never counts, and only the controller closes one.

Everything here is a pure rule over the entries that
``query_project_history(..., "opinions")`` returns, so it is tested on its
own; :mod:`marcus_toolkit.tending` applies it against a live board.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CANDIDATE_PREFIX = "candidate."
PROPOSAL_PREFIX = "propose."
#: "Only two agents need to support a promotion." (Larry, 2026-11-06)
THRESHOLD = 2
#: The superseded seconds window (Larry, 2026-11-06, "evaporation after 4.5
#: minutes"), kept so the runs measured with it stay reproducible.
EVAPORATION_SECONDS = 270
#: "views, k=3" (Larry, 2026-11-06): a candidate evaporates once this many
#: distinct agents have been shown it and then had a turn in which they could
#: have supported it and did not. A candidate no agent has declined never
#: evaporates, so "open" is a terminal state in the record.
DECLINES_NEEDED = 3
#: A candidate born from a corroborated observation carries its observers as
#: supporters and promotes once this many seconds pass with no objection.
VETO_SECONDS = 120
POST_MARK = "CANDIDATE: "
OBJECT_MARK = "OBJECT:"
#: How an agent says it cannot judge a candidate: neither support nor an
#: objection, and a decline the moment it is written.
ABSTAIN_MARK = "NO EVIDENCE:"
#: What the controller posts on a candidate's own subject once it is closed.
CLOSE_MARK = "CLOSED:"
#: What became of a promoted candidate's task, posted by the controller on the
#: candidate's subject whenever it changes: ``TASK: open``, ``TASK: held by
#: <agent>``, ``TASK: blocked``, ``TASK: done``.
TASK_MARK = "TASK:"
TASK_STATES = ("open", "held", "blocked", "done")
#: The label every promoted or proposed task carries, so a record can tell
#: seeded work from work the crew ratified.
PLANNED_LABEL = "planned"


def task_state(task: dict[str, Any]) -> tuple[str, str | None]:
    """
    Read one of ``TASK_STATES`` and the holder off a board task row.

    Parameters
    ----------
    task : Dict[str, Any]
        A task row with ``status`` and ``assigned_to``.

    Returns
    -------
    Tuple[str, Optional[str]]
        ``("done", None)``, ``("blocked", None)``, ``("held", agent)`` for a
        task in progress or assigned, else ``("open", None)``.
    """
    status = str(task.get("status") or "").strip().lower()
    holder = str(task.get("assigned_to") or "").strip() or None
    if status in ("done", "completed"):
        return "done", None
    if status == "blocked":
        return "blocked", None
    if status == "in_progress" or holder:
        return "held", holder
    return "open", None


def task_line(state: str, held_by: str | None = None) -> str:
    """Render the controller's status line: ``TASK: held by Ash``, ``TASK: blocked``."""
    if state == "held" and held_by:
        return f"{TASK_MARK} held by {held_by}"
    return f"{TASK_MARK} {state}"


def parse_task_line(text: str) -> tuple[str, str | None] | None:
    """
    Read the state and holder a ``TASK:`` line carries, or ``None`` for other text.

    Parameters
    ----------
    text : str
        An opinion's text.

    Returns
    -------
    Optional[Tuple[str, Optional[str]]]
        ``("held", "Ash")``, ``("blocked", None)``; a line with an unknown
        state gives ``None``, so a typo never reads as a status.
    """
    body = str(text or "").strip()
    if not body.upper().startswith(TASK_MARK):
        return None
    rest = body[len(TASK_MARK) :].strip()
    if rest.lower().startswith("held by "):
        holder = rest[len("held by ") :].strip()
        return ("held", holder or None)
    state = rest.lower()
    return (state, None) if state in TASK_STATES else None


@dataclass
class Candidate:
    """One open candidate as read off the board."""

    subject: str
    spec: dict[str, Any]
    posted_at: str
    supporters: list[str] = field(default_factory=list)
    objectors: list[str] = field(default_factory=list)
    #: Agents whose agreement on an observation produced the candidate; they
    #: count as supporters, and support born this way waits the veto window.
    observers: list[str] = field(default_factory=list)
    #: Agents that answered ``NO EVIDENCE:``.
    abstainers: list[str] = field(default_factory=list)
    #: When each agent's current answer landed, by agent.
    answered_at: dict[str, str] = field(default_factory=dict)

    def support_complete_at(self) -> str | None:
        """
        Return when the last supporter needed to reach the threshold posted.

        The settling window runs from here, not from the posting.

        Returns
        -------
        Optional[str]
            The timestamp, or ``None`` when support is short of the threshold
            or the record carries no times.
        """
        observers = set(self.observers)
        times = sorted(
            self.answered_at[agent]
            for agent in self.supporters
            if agent not in observers and self.answered_at.get(agent)
        )
        return times[THRESHOLD - 1] if len(times) >= THRESHOLD else None

    def age_seconds(self, now: str) -> float:
        """Return the seconds between the controller's posting and ``now``."""
        return (_when(now) - _when(self.posted_at)).total_seconds()


def _when(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _slug(name: str) -> str:
    return ".".join(t for t in re.split(r"[^a-z0-9]+", str(name).lower()) if t)


def candidate_subject(name: str) -> str:
    """Return the reserved subject a candidate with this name is posted under."""
    return CANDIDATE_PREFIX + _slug(name)


def post_text(spec: dict[str, Any]) -> str:
    """Render the controller's posting: the spec as JSON after the mark."""
    return POST_MARK + json.dumps(spec, ensure_ascii=False)


def parse_candidate(
    entry: dict[str, Any], controller_ids: set[str]
) -> Candidate | None:
    """
    Read one opinions entry as a candidate, or ``None`` if it is not one.

    Parameters
    ----------
    entry : Dict[str, Any]
        One entry from ``query_project_history(..., "opinions")``: ``subject``
        and ``opinions`` (each with ``agent_id``, ``what``, ``timestamp``).
    controller_ids : Set[str]
        The controller processes' agent ids. Their postings define the
        candidate and never count as support.

    Returns
    -------
    Optional[Candidate]
        The candidate with its supporters and objectors in order of first
        appearance, each agent counted once; an agent's latest word wins.
        A closed candidate parses as ``None``.
    """
    subject = str(entry.get("subject") or "")
    if not subject.startswith(CANDIDATE_PREFIX):
        return None
    opinions = sorted(
        entry.get("opinions") or [], key=lambda o: str(o.get("timestamp") or "")
    )
    spec: dict[str, Any] | None = None
    posted_at: str | None = None
    for o in opinions:
        if o.get("agent_id") in controller_ids and str(o.get("what") or "").startswith(
            POST_MARK
        ):
            try:
                spec = json.loads(str(o["what"])[len(POST_MARK) :])
            except json.JSONDecodeError:
                continue
            posted_at = str(o.get("timestamp"))
            break
    if spec is None or posted_at is None:
        return None
    if any(
        o.get("agent_id") in controller_ids
        and str(o.get("what") or "").strip().upper().startswith(CLOSE_MARK)
        for o in opinions
    ):
        return None
    latest: dict[str, str] = {}
    latest_at: dict[str, str] = {}
    order: list[str] = []
    for o in opinions:
        agent = str(o.get("agent_id") or "")
        if not agent or agent in controller_ids:
            continue
        if agent not in latest:
            order.append(agent)
        latest[agent] = str(o.get("what") or "").strip()
        stamp = str(o.get("timestamp") or "").strip()
        if stamp:
            latest_at[agent] = stamp

    def mark(agent: str) -> str:
        said = latest[agent].strip().upper()
        if said.startswith(OBJECT_MARK):
            return "object"
        if said.startswith(ABSTAIN_MARK):
            return "abstain"
        return "support"

    marks = {a: mark(a) for a in order}
    supporters = [a for a in order if marks[a] == "support"]
    objectors = [a for a in order if marks[a] == "object"]
    abstainers = [a for a in order if marks[a] == "abstain"]
    observers = [
        str(a)
        for a in (spec.get("observers") or [])
        if str(a) and str(a) not in controller_ids
    ]
    for agent in observers:
        answered = agent in supporters or agent in objectors or agent in abstainers
        if not answered:
            supporters.append(agent)
    proposer = str(spec.get("proposer") or "")
    if proposer and proposer not in controller_ids:
        if (
            proposer not in supporters
            and proposer not in objectors
            and proposer not in abstainers
        ):
            supporters.insert(0, proposer)
        proposed_at = str(spec.get("proposed_at") or "").strip()
        if proposed_at and proposer not in latest_at:
            latest_at[proposer] = proposed_at
    return Candidate(
        subject=subject,
        spec=spec,
        posted_at=posted_at,
        supporters=supporters,
        objectors=objectors,
        observers=observers,
        abstainers=abstainers,
        answered_at=latest_at,
    )


def decide(
    candidate: Candidate,
    now: str,
    veto_seconds: int = VETO_SECONDS,
    evaporation_seconds: int = EVAPORATION_SECONDS,
    attention_seconds: float | None = None,
    declined_by: int | None = None,
    declines_needed: int = DECLINES_NEEDED,
    awaiting: int | None = None,
    settling_seconds: int = 0,
) -> str:
    """
    Apply the fixed rule: ``"promote"``, ``"wait"`` or ``"evaporate"``.

    A candidate promotes when at least two distinct agents support it and
    supporters outnumber objectors (a tie does not carry; declines are not
    votes against). Support born from observers promotes only after
    ``veto_seconds``. There is no clock-based promotion: silence never puts
    work on the board.

    Parameters
    ----------
    candidate : Candidate
        The candidate, with its supporters and objectors.
    now : str
        The current time, ISO-8601.
    veto_seconds : int
        The window before an observation-born candidate promotes.
    evaporation_seconds : int
        The superseded seconds window, used only when ``declined_by`` is ``None``.
    attention_seconds : Optional[float]
        The superseded attention clock.
    declined_by : Optional[int]
        Distinct agents that were shown the candidate and then had a turn in
        which they could have supported it and did not. When given, this is
        what evaporates a candidate and nothing else does.
    declines_needed : int
        How many declines evaporate a candidate.
    awaiting : Optional[int]
        Distinct agents that could still answer. At zero the candidate is
        settled whichever way it falls, so a contested one cannot hang.
    settling_seconds : int
        How long after support reaches the threshold before promotion, so a
        late objection can still land. An objection inside the window blocks.

    Returns
    -------
    str
    """
    age = candidate.age_seconds(now)
    if declined_by is not None:
        expired = declined_by >= declines_needed
    else:
        aged = attention_seconds if attention_seconds is not None else age
        expired = aged > evaporation_seconds
    exhausted = awaiting is not None and awaiting <= 0
    observers = set(candidate.observers)
    posted = {s for s in candidate.supporters if s not in observers}
    objectors = len(set(candidate.objectors))
    if len(posted) >= THRESHOLD and len(posted) > objectors:
        if settling_seconds <= 0 or exhausted:
            return "promote"
        complete = candidate.support_complete_at()
        if complete is None:
            return "promote"
        waited = (_when(now) - _when(complete)).total_seconds()
        return "promote" if waited >= settling_seconds else "wait"
    if (
        len(set(candidate.supporters)) >= THRESHOLD
        and len(set(candidate.supporters)) > objectors
    ):
        return "promote" if age >= veto_seconds else "wait"
    return "evaporate" if expired or exhausted else "wait"


def can_post(open_count: int, crew: int) -> bool:
    """Return whether another candidate may open: at most the crew size at once."""
    return open_count < crew


def default_criterion(name: str) -> str:
    """Return the completion criterion a spec gets when it names none."""
    return (
        f"The condition described in '{name}' holds, and the completion report"
        " says what was done and carries the evidence for it"
    )


def candidate_from_proposal(
    entry: dict[str, Any], labels: Sequence[str] = (PLANNED_LABEL,)
) -> tuple[dict[str, Any], str]:
    """
    Turn an agent's ``propose.*`` opinion into a candidate spec.

    The proposer is the candidate's first supporter: the proposal is its
    evidence. One more distinct agent promotes it.

    Parameters
    ----------
    entry : Dict[str, Any]
        The ``propose.*`` opinions entry.
    labels : Sequence[str]
        The labels the task carries; never a ``build``-family label.

    Returns
    -------
    Tuple[Dict[str, Any], str]
        The seed spec and the proposer.
    """
    subject = str(entry.get("subject") or "")
    name = " ".join(subject[len(PROPOSAL_PREFIX) :].split(".")).strip() or "proposal"
    opinions = sorted(
        entry.get("opinions") or [], key=lambda o: str(o.get("timestamp") or "")
    )
    first = opinions[0] if opinions else {}
    proposer = str(first.get("agent_id") or "")
    description = str(first.get("what") or "").strip() or name
    spec = {
        "name": name,
        "description": description,
        "labels": list(labels),
        "completion_criteria": [default_criterion(name)],
        "estimated_hours": 0.4,
        "priority": "medium",
        "because": [subject],
        # Not a create_tasks key, so it never reaches the board; it is the
        # time the proposer's support landed, for the settling window.
        "proposed_at": str(first.get("timestamp") or "").strip(),
    }
    return spec, proposer


def candidate_from_observation(
    entry: dict[str, Any],
    build_seed: Callable[[dict[str, Any]], dict[str, Any]],
    labels: Sequence[str] = (PLANNED_LABEL,),
) -> dict[str, Any]:
    """
    Turn an observation subject two distinct agents posted on into a candidate spec.

    Parameters
    ----------
    entry : Dict[str, Any]
        The observation's opinions entry, with ``agents``.
    build_seed : Callable[[Dict[str, Any]], Dict[str, Any]]
        The extension's rule for the task an observation becomes: its name,
        description and fixed criteria. The toolkit has no domain to supply one.
    labels : Sequence[str]
        Labels every such task carries.

    Returns
    -------
    Dict[str, Any]
        The spec, with the observers carried so they count as supporters and
        the veto window applies.
    """
    spec = dict(build_seed(entry))
    have = [str(label) for label in (spec.get("labels") or [])]
    for label in labels:
        if label not in have:
            have.append(label)
    spec["labels"] = have
    spec["observers"] = sorted({str(a) for a in (entry.get("agents") or []) if str(a)})
    spec["because"] = [str(entry.get("subject"))]
    if not spec.get("completion_criteria"):
        spec["completion_criteria"] = [default_criterion(str(spec.get("name") or ""))]
    return spec


def lineage_line(
    spec: dict[str, Any], supporters: Iterable[str], authors: dict[str, list[str]]
) -> str:
    """
    Render the paragraph a promoted task carries: its sources and its supporters.

    Avoids the words plan, document, check, verify, test and resolve, which an
    older kernel's phase classifier read as task phases.

    Parameters
    ----------
    spec : Dict[str, Any]
        The candidate's spec; ``because`` names its sources.
    supporters : Iterable[str]
        Who supported it.
    authors : Dict[str, List[str]]
        Marker subject to its authors.

    Returns
    -------
    str
    """
    parts = []
    for source in spec.get("because") or []:
        who = authors.get(source)
        parts.append(f"{source} ({', '.join(who)})" if who else str(source))
    sources = ", ".join(parts) if parts else "the objective and the board"
    return f"Lineage: from {sources}; supported by {', '.join(supporters)}"
