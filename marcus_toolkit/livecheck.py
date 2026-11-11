"""
The toolbox's own live check: a real server, a scripted agent, every reply read back.

What it proves, the way every extension's free rehearsal proves it: a server
under test starts with no key and its model off; a project is seeded with
two tasks, the second depending on the first; one scripted agent registers,
pulls the first, reports with evidence, pulls the second (now claimable),
closes it, and hears the drain; the sampler read the board from outside
before and after; the server log carries no model-provider host.

Usage::

    python -m marcus_toolkit.livecheck --checkout ~/dev/marcus --out livecheck-runs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import integrity, measure, record, sampler, seed, server
from .agent import BoardAgent, jsonl_logger
from .client import MarcusMCPClient

TASKS: list[dict[str, Any]] = [
    {
        "name": "Say where you are",
        "description": "Report your position on the board; nothing else to do.",
        "labels": ["livecheck", "seeded"],
        "completion_criteria": ["The completion report says where the agent is"],
        "estimated_hours": 0.1,
        "priority": "high",
    },
    {
        "name": "Say it again, now that the first report is in",
        "description": "Only claimable once the first task is done.",
        "labels": ["livecheck", "seeded"],
        "depends_on": ["Say where you are"],
        "completion_criteria": ["The completion report repeats the position"],
        "estimated_hours": 0.1,
        "priority": "medium",
    },
]


def run(checkout: Path, out: Path, python: str = sys.executable) -> dict[str, Any]:
    """
    Run the live check and return its verdict.

    Parameters
    ----------
    checkout : Path
        A Marcus checkout to serve from.
    out : Path
        The ``runs`` root; a ``<stamp>/`` directory is created under it.
    python : str
        The interpreter that runs the server.

    Returns
    -------
    Dict[str, Any]
        ``{"PASS": bool, "checks": {...}, "record": str}``.
    """
    rec = record.RunRecord(out)
    data_dir = rec.dir / "data"
    port = server.free_port()
    config_path = rec.dir / "config_marcus.json"
    server.write_config(
        config_path,
        port=port,
        project_name="runner-livecheck",
        sqlite_db_path=data_dir / "kanban.db",
    )
    version = integrity.git_version(checkout)
    rec.write("marcus-version.txt", integrity.format_version(version))
    for key, value in [
        ("run_at", rec.stamp),
        ("kind", "marcus-toolkit live check"),
        ("port", port),
        ("checkout", f"{checkout} @ {version['commit']} ({version['dirty']} dirty)"),
        ("ai", "off, keys stripped"),
    ]:
        rec.config_line(key, value)

    srv = server.ServerUnderTest(
        checkout, data_dir, config_path, port, rec.dir / "server.log", python=python
    )
    checks: dict[str, bool] = {}
    detail: dict[str, Any] = {}
    try:
        srv.start(wait_seconds=120)
        client = MarcusMCPClient(srv.url, timeout=120)
        if not client.connect():
            raise RuntimeError("could not connect to the server under test")
        seeded = seed.seed_project(
            client,
            name="runner-livecheck",
            description="the toolbox's own live check",
            tasks=TASKS,
            project_root=rec.dir / "project-root",
            objective="Say where you are; the board is the only channel.",
        )
        rec.config_line("project_id", seeded.project_id)
        board_db = data_dir / "kanban.db"
        before = sampler.sample(board_db, seeded.project_id)

        state_dir = rec.dir / "state"
        agent = BoardAgent(
            client,
            "probe-1",
            seeded.project_id,
            role="probe",
            skills=["probe"],
            log=jsonl_logger(state_dir / "probe-1" / "board-calls.jsonl"),
            hold_path=state_dir / "probe-1" / "board-hold.json",
        )
        first = agent.pull()
        detail["first_pull"] = first.get("status")
        if first.get("status") == "assigned":
            agent.report(
                "in_progress",
                "at the start line",
                progress=30,
                subject="position.start",
            )
            agent.decide(
                "stayed at the start line",
                "the task asks only for a report",
                subject="position.start",
            )
            agent.opinion(
                "position.start",
                "the start line is where the board says it is",
                "I looked",
            )
            r1 = agent.report(
                "completed",
                "I am at the start line",
                evidence={"position": "start line", "task": first["task"].get("name")},
            )
            detail["first_close"] = r1.get("success")
        second = agent.pull()
        detail["second_pull"] = second.get("status")
        if second.get("status") == "assigned":
            r2 = agent.report(
                "completed",
                "Still at the start line",
                evidence={"position": "start line", "task": second["task"].get("name")},
            )
            detail["second_close"] = r2.get("success")
        third = agent.pull()
        detail["third_pull"] = third.get("status")
        after = sampler.sample(board_db, seeded.project_id)

        calls = measure.read_calls(state_dir)
        pulls = measure.pulls(calls)
        board = measure.board_from_db(board_db, seeded.project_id)
        model_calls = srv.model_calls()
        measurements = {
            "project_id": seeded.project_id,
            "board": board,
            "board_calls": measure.board_calls(calls),
            "pulls": pulls,
            "sampler_before": before,
            "sampler_after": after,
            "model_calls_in_server_log": model_calls,
            "detail": detail,
        }
        rec.write("measurements.json", json.dumps(measurements, indent=2) + "\n")
        checks = {
            "board holds 3 cards (2 seeded + About)": board["total_tasks"] == 3,
            "the sampler saw 1 claimable and 1 unclaimable before": before[
                "tasks_claimable"
            ]
            == 1
            and before["tasks_unclaimable"] == 1,
            "first pull assigned the first task": first.get("status") == "assigned"
            and (first.get("task") or {}).get("name") == TASKS[0]["name"],
            "the first close was accepted": detail.get("first_close") is True,
            "second pull assigned the dependent task": second.get("status")
            == "assigned"
            and (second.get("task") or {}).get("name") == TASKS[1]["name"],
            "the second close was accepted": detail.get("second_close") is True,
            "the third pull heard the drain": third.get("status") == "drained",
            "both seeded cards are done on the board": board["completed"] == 3,
            "the sampler saw nothing claimable after": after["tasks_claimable"] == 0,
            "every board call was read back": bool(calls)
            and all("reply" in c for c in calls),
            "the server made zero model calls": model_calls == 0,
        }
    finally:
        srv.stop()
        rec.copy_in(data_dir / "kanban.db", "kanban.db")
    verdict = {
        "PASS": all(checks.values()) and bool(checks),
        "checks": checks,
        "record": str(rec.dir),
    }
    rec.write("VERDICT.json", json.dumps(verdict, indent=2) + "\n")
    return verdict


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exit 0 on PASS."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", required=True, help="a Marcus checkout to serve from"
    )
    parser.add_argument("--out", default="livecheck-runs", help="where the record goes")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    verdict = run(Path(args.checkout).resolve(), Path(args.out), args.python)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
