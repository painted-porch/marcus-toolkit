"""
The tender: a controller process that applies the candidate rules to a board.

Every poll it reads the board's opinions, the board's tasks, and the agents'
view records, and runs one tending pass (``tending.tend_candidates``):
promotions through ``create_tasks``, evaporations by declines, proposals and
corroborated observations turned into candidates, a promoted task's status
posted on its candidate's subject, one event per change in ``candidates.jsonl``.
On its first pass it may post candidates from a script, which is the model-free
stand-in for a planning model; a model coordinator posts through the same
:func:`marcus_toolkit.tending.post_candidate`. On SIGTERM or SIGINT it writes an
``open_at_end`` row for every candidate still open and exits.

Usage::

    python -m marcus_toolkit.tender --url http://127.0.0.1:PORT/mcp --project ID \\
        --kanban-db data/kanban.db --state-dir runs/x/state \\
        --out runs/x/candidates.jsonl \\
        --crew 5 --post candidates-to-post.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import signal
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import measure, tending, views
from .candidates import PLANNED_LABEL, can_post
from .client import MarcusMCPClient


def now_iso() -> str:
    """Return the current UTC time in ISO-8601."""
    return datetime.now(UTC).isoformat()


def board_tasks(kanban_db: Path, project_id: str) -> list[dict[str, Any]]:
    """
    Read a project's task rows off the board file.

    Parameters
    ----------
    kanban_db : Path
        The board.
    project_id : str
        The project.

    Returns
    -------
    List[Dict[str, Any]]
        Rows with ``id``, ``name``, ``status`` and ``assigned_to``; empty when
        unreadable.
    """
    if not kanban_db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{kanban_db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "select id, name, status, assigned_to from tasks where project_id = ?",
            (project_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [
        {"id": r[0], "name": r[1], "status": r[2], "assigned_to": r[3]} for r in rows
    ]


class Tender:
    """
    One controller tending one project's candidates.

    Parameters
    ----------
    client : Any
        A connected board client with ``call_tool``.
    project_id : str
        The project tended.
    controller_id : str
        The controller's agent id.
    crew : int
        The cap on open candidates.
    out : Path
        ``candidates.jsonl``.
    state_dir : Optional[Path]
        Where the agents' ``board-calls.jsonl`` files are, for the view tally.
    kanban_db : Optional[Path]
        The board file, for task names and statuses.
    labels : Sequence[str]
        Labels proposal- and observation-born tasks carry.
    declines_needed, settling_seconds, veto_seconds : int
        The rule's knobs.
    """

    def __init__(
        self,
        client: Any,
        project_id: str,
        controller_id: str,
        crew: int,
        out: Path,
        state_dir: Path | None = None,
        kanban_db: Path | None = None,
        labels: Sequence[str] = (PLANNED_LABEL,),
        declines_needed: int = 3,
        settling_seconds: int = 0,
        veto_seconds: int = 120,
        build_seed: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.build_seed = build_seed
        self.client = client
        self.project_id = project_id
        self.controller_id = controller_id
        self.crew = crew
        self.out = out
        self.state_dir = state_dir
        self.kanban_db = kanban_db
        self.labels = list(labels)
        self.declines_needed = declines_needed
        self.settling_seconds = settling_seconds
        self.veto_seconds = veto_seconds
        self.handled: set[str] = set()
        self.answered: dict[str, list[str]] = {}
        self.attempts: dict[str, int] = {}
        self.last_open: list[dict[str, Any]] = []
        self.last_views: dict[str, dict[str, Any]] | None = None
        self.passes = 0

    def entries(self) -> list[dict[str, Any]]:
        """Read the board's opinions entries."""
        reply = (
            self.client.call_tool(
                "query_project_history",
                {"project_id": self.project_id, "query_type": "opinions", "limit": 200},
            )
            or {}
        )
        data = reply.get("data") if reply.get("success") is not False else None
        return [e for e in (data or []) if isinstance(e, dict)]

    def post_scripted(self, specs: list[dict[str, Any]]) -> list[str]:
        """
        Post candidates from a script, up to the cap.

        Parameters
        ----------
        specs : List[Dict[str, Any]]
            Task specs with ``because``.

        Returns
        -------
        List[str]
            The subjects posted.
        """
        posted: list[str] = []
        open_count = len(self.last_open)
        for spec in specs:
            if not can_post(open_count, self.crew):
                break
            posted.append(
                tending.post_candidate(
                    self.client, self.controller_id, spec, self.out, now_iso()
                )
            )
            open_count += 1
        return posted

    def run_pass(self) -> dict[str, list[Any]]:
        """Run one tending pass and return its actions."""
        entries = self.entries()
        authors = {
            str(e.get("subject")): [str(a) for a in (e.get("agents") or [])]
            for e in entries
            if e.get("subject")
        }
        tasks = board_tasks(self.kanban_db, self.project_id) if self.kanban_db else []
        tally = None
        if self.state_dir is not None:
            calls = measure.read_calls(self.state_dir)
            tally = views.tally(calls)
        actions = tending.tend_candidates(
            self.client,
            entries,
            now=now_iso(),
            crew=self.crew,
            controller_id=self.controller_id,
            out=self.out,
            handled=self.handled,
            authors=authors,
            board_names=[str(t.get("name") or "") for t in tasks],
            labels=self.labels,
            veto_seconds=self.veto_seconds,
            views=tally,
            declines_needed=self.declines_needed,
            settling_seconds=self.settling_seconds,
            answered=self.answered,
            attempts=self.attempts,
            tasks=tasks,
            observations=self.build_seed is not None,
            build_seed=self.build_seed,
        )
        self.last_open = list(actions.get("open_specs") or [])
        self.last_views = tally
        self.passes += 1
        return actions

    def flush_open(self) -> int:
        """Write an ``open_at_end`` row per candidate still open; return how many."""
        rows = tending.open_at_end_records(
            self.last_open, self.last_views, self.answered, now_iso()
        )
        if rows:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            with self.out.open("a") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
        return len(rows)


def load_build_seed(spec: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """
    Import ``module:function`` from the current path.

    Parameters
    ----------
    spec : str
        ``package.module:function``.

    Returns
    -------
    Callable
        The extension's observation rule.
    """
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"--build-seed needs module:function, got {spec!r}")
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    if not callable(func):
        raise ValueError(f"{spec} is not callable")
    return func  # type: ignore[no-any-return]


class Stopped(Exception):
    """Raised by the signal handler to end the loop."""


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: tend until stopped or until the deadline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--controller-id", default="planner")
    parser.add_argument("--crew", type=int, default=5)
    parser.add_argument("--out", required=True, help="candidates.jsonl")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--kanban-db", default=None)
    parser.add_argument(
        "--post", default=None, help="a JSON list of specs to post on the first pass"
    )
    parser.add_argument("--labels", default=PLANNED_LABEL, help="comma-separated")
    parser.add_argument(
        "--build-seed",
        default=None,
        help="module:function turning a corroborated observation entry into a task spec"
        " (or None for no card); turns the observation route on",
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-minutes", type=float, default=30.0)
    parser.add_argument("--declines-needed", type=int, default=3)
    parser.add_argument("--settling-seconds", type=int, default=0)
    parser.add_argument("--veto-seconds", type=int, default=120)
    args = parser.parse_args(argv)

    client = MarcusMCPClient(args.url, timeout=60)
    if not client.connect():
        print(f"tender: could not connect: {client.last_error}", file=sys.stderr)
        return 2
    if not tending.ensure_registered(client, args.controller_id, args.project):
        print("tender: registration refused", file=sys.stderr)
        return 2
    build_seed = load_build_seed(args.build_seed) if args.build_seed else None
    tender = Tender(
        client,
        args.project,
        args.controller_id,
        args.crew,
        Path(args.out),
        state_dir=Path(args.state_dir) if args.state_dir else None,
        kanban_db=Path(args.kanban_db) if args.kanban_db else None,
        labels=[s.strip() for s in args.labels.split(",") if s.strip()],
        declines_needed=args.declines_needed,
        settling_seconds=args.settling_seconds,
        veto_seconds=args.veto_seconds,
        build_seed=build_seed,
    )

    def stop(signum: int, frame: Any) -> None:
        raise Stopped()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    deadline = time.time() + args.max_minutes * 60
    try:
        if args.post:
            specs = json.loads(Path(args.post).read_text())
            posted = tender.post_scripted(list(specs))
            print(f"tender: posted {len(posted)} scripted candidate(s)", flush=True)
        while time.time() < deadline:
            actions = tender.run_pass()
            moved = {
                k: v
                for k, v in actions.items()
                if v and k not in ("open_specs", "settled_names")
            }
            if moved:
                print(f"tender: pass {tender.passes}: {json.dumps(moved)}", flush=True)
            time.sleep(args.poll_seconds)
    except Stopped:
        pass
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        flushed = tender.flush_open()
        print(
            f"tender: stopped after {tender.passes} passes; {flushed} open at end",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
