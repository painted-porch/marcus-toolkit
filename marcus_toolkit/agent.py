"""
A pull-loop agent shell in Python: the Node client's ``board.js``, for Python runtimes.

Register with a project (Marcus hands work only to a registered agent), pull,
absorb "ask again" replies without a model turn, hold the task and its lease
epoch across a restart, report with the lease epoch and evidence, leave
markers, record decisions, read the board. Every call is one record with the
six fields the Minecraft runs measure: ``at``, ``agent``, ``tool``, ``args``,
``outcome``, ``ms``, ``reply``.

Nothing here talks to another agent. The board is the only channel.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class ToolClient(Protocol):
    """What the shell needs from a client: a tool call returning a reply or None."""

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Call a tool and return its reply body, or ``None``."""


def outcome_of(tool: str, reply: dict[str, Any]) -> str:
    """
    Classify a reply the way the Minecraft harness does.

    Parameters
    ----------
    tool : str
        The tool called.
    reply : Dict[str, Any]
        The reply body.

    Returns
    -------
    str
        ``transport_error``, ``drained``, ``assigned``, ``retry``, ``failed`` or ``ok``.
    """
    if reply.get("transport_error") or reply.get("rpc_error"):
        return "transport_error"
    if reply.get("status") == "BOARD_DRAINED":
        return "drained"
    if tool == "request_next_task":
        return "assigned" if reply.get("success") is True else "retry"
    return "failed" if reply.get("success") is False else "ok"


def jsonl_logger(path: Path) -> Callable[[dict[str, Any]], None]:
    """
    Append one JSON line per record to ``path``, flushed at once.

    Parameters
    ----------
    path : Path
        ``<state>/<agent>/board-calls.jsonl``; parents are created.

    Returns
    -------
    Callable[[Dict[str, Any]], None]
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(record: dict[str, Any]) -> None:
        with path.open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return log


class BoardAgent:
    """
    One agent's board client.

    Parameters
    ----------
    client : ToolClient
        A connected MCP client.
    agent_id : str
        The agent's id on the board.
    project_id : str
        The project it registers to.
    role : str
        The role string sent on registration (not enforced by the kernel).
    skills : Sequence[str]
        The skills sent on registration.
    project_root : str
        Where artifacts are written; a directory that is not a repository.
    log : Optional[Callable[[Dict[str, Any]], None]]
        Receives one record per board call.
    hold_path : Optional[Path]
        Where the held task and its lease epoch persist across a restart.
    sleep : Callable[[float], None]
        The sleep used to absorb retries; tests pass a stub.
    now : Callable[[], float]
        The clock, in seconds.
    """

    def __init__(
        self,
        client: ToolClient,
        agent_id: str,
        project_id: str,
        *,
        role: str = "worker",
        skills: Sequence[str] = ("worker",),
        project_root: str = "",
        log: Callable[[dict[str, Any]], None] | None = None,
        hold_path: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.agent_id = agent_id
        self.project_id = project_id
        self.role = role
        self.skills = list(skills)
        self.project_root = project_root
        self.log = log
        self.hold_path = hold_path
        self.sleep = sleep
        self.now = now
        self.registered = False
        self.objective: str | None = None
        self.task: dict[str, Any] | None = None
        self.lease_epoch: int | None = None
        if hold_path is not None and hold_path.is_file():
            held = json.loads(hold_path.read_text())
            self.task = held.get("task")
            self.lease_epoch = held.get("lease_epoch")

    def _remember(self) -> None:
        if self.hold_path is None:
            return
        if self.task:
            self.hold_path.parent.mkdir(parents=True, exist_ok=True)
            self.hold_path.write_text(
                json.dumps({"task": self.task, "lease_epoch": self.lease_epoch})
            )
        elif self.hold_path.exists():
            self.hold_path.unlink()

    def board(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Call one tool, timing and logging it.

        Parameters
        ----------
        tool : str
            The tool name.
        args : Dict[str, Any]
            The arguments.

        Returns
        -------
        Dict[str, Any]
            The reply body; a missing reply becomes a transport error.
        """
        started = self.now()
        reply = self.client.call_tool(tool, args)
        if reply is None:
            reply = {"success": False, "error": "no reply", "transport_error": True}
        if self.log:
            self.log(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "agent": self.agent_id,
                    "tool": tool,
                    "args": args,
                    "outcome": outcome_of(tool, reply),
                    "ms": int((self.now() - started) * 1000),
                    "reply": reply,
                }
            )
        return reply

    def register(self) -> dict[str, Any]:
        """Register with the project and read its objective once."""
        reply = self.board(
            "register_agent",
            {
                "agent_id": self.agent_id,
                "name": self.agent_id,
                "role": self.role,
                "skills": self.skills,
                "project_id": self.project_id,
            },
        )
        if reply.get("success") is False:
            return reply
        self.registered = True
        primer = self.board(
            "query_project_history",
            {
                "project_id": self.project_id,
                "query_type": "opinions",
                "kind": "objective",
                "limit": 1,
            },
        )
        data = primer.get("data") if primer.get("success") is not False else None
        try:
            what = data[0]["opinions"][0]["what"] if data else None
        except (KeyError, IndexError, TypeError):
            what = None
        if isinstance(what, str) and what.strip():
            self.objective = what.strip()
        return reply

    def pull(
        self,
        absorb: bool = False,
        max_asks: int | None = None,
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        """
        Ask for work.

        Parameters
        ----------
        absorb : bool
            Wait out "ask again" replies here, one board call per ask.
        max_asks : Optional[int]
            Stop absorbing after this many asks.
        wait_seconds : Optional[float]
            Override the reply's ``retry_after_seconds``.

        Returns
        -------
        Dict[str, Any]
            ``status`` is ``assigned`` (with ``task``, ``lease_epoch``, ``context``,
            ``opinions``, maybe ``objective``), ``retry``, ``drained``, ``error`` or
            ``holding``.
        """
        if not self.registered:
            refused = self.register()
            if refused.get("success") is False:
                return {"status": "error", "reply": refused}
        if self.task:
            return {"status": "holding", "task": self.task}
        asks = 0
        while True:
            reply = self.board("request_next_task", {"agent_id": self.agent_id})
            asks += 1
            task = reply.get("task")
            if reply.get("success") is True and isinstance(task, dict):
                self.task = {"id": task.get("id"), "name": task.get("name")}
                self.lease_epoch = reply.get("lease_epoch")
                self._remember()
                context = self.board("get_task_context", {"task_id": task.get("id")})
                opinions = self.board(
                    "query_project_history",
                    {
                        "project_id": self.project_id,
                        "query_type": "opinions",
                        "limit": 20,
                    },
                )
                out: dict[str, Any] = {
                    "status": "assigned",
                    "task": task,
                    "lease_epoch": self.lease_epoch,
                    "context": context,
                    "opinions": opinions,
                }
                if self.objective:
                    out["objective"] = self.objective
                return out
            if reply.get("status") == "BOARD_DRAINED":
                return {"status": "drained", "reply": reply}
            if reply.get("transport_error") or reply.get("rpc_error"):
                return {"status": "error", "reply": reply}
            if not absorb or (max_asks is not None and asks >= max_asks):
                return {"status": "retry", "reply": reply}
            wait = (
                wait_seconds
                if wait_seconds is not None
                else max(1.0, float(reply.get("retry_after_seconds") or 30))
            )
            self.sleep(wait)

    def report(
        self,
        status: str,
        message: str,
        *,
        progress: int | None = None,
        subject: str | None = None,
        observation: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Report on the held task; completion or blocking clears the hold.

        Parameters
        ----------
        status : str
            ``in_progress``, ``completed`` or ``blocked``.
        message : str
            What was done.
        progress : Optional[int]
            Percent; 100 for completed, else 50 by default.
        subject : Optional[str]
            A marker subject, so the report is also an opinion.
        observation : Optional[str]
            The marker's text.
        evidence : Optional[Dict[str, Any]]
            The structured evidence the close carries.

        Returns
        -------
        Dict[str, Any]
        """
        if not self.task:
            return {"success": False, "error": "You hold no task."}
        args: dict[str, Any] = {
            "agent_id": self.agent_id,
            "task_id": self.task["id"],
            "status": status,
            "progress": (
                progress
                if progress is not None
                else (100 if status == "completed" else 50)
            ),
            "message": message,
        }
        if subject and subject.strip():
            args["subject"] = subject.strip()
        if observation and observation.strip():
            args["observation"] = observation.strip()
        if evidence:
            args["evidence"] = evidence
        if self.lease_epoch:
            args["lease_epoch"] = self.lease_epoch
        reply = self.board("report_task_progress", args)
        if reply.get("success") is not False and status != "in_progress":
            self.task = None
            self.lease_epoch = None
            self._remember()
        return reply

    def _registered(self) -> dict[str, Any] | None:
        """Register first if needed; return the refusal when registration fails."""
        if self.registered:
            return None
        reply = self.register()
        return reply if reply.get("success") is False else None

    def opinion(
        self,
        subject: str,
        what: str,
        why: str = "",
        impact: str = "",
        about_task: bool = False,
    ) -> dict[str, Any]:
        """Leave a marker on the board; registers first when needed (K6)."""
        refused = self._registered()
        if refused is not None:
            return refused
        args: dict[str, Any] = {
            "agent_id": self.agent_id,
            "subject": subject,
            "what": what,
            "why": why,
            "impact": impact,
        }
        if about_task and self.task:
            args["task_id"] = self.task["id"]
        return self.board("give_opinion", args)

    def decide(
        self, what: str, why: str, subject: str | None = None, kind: str = "decision"
    ) -> dict[str, Any]:
        """Record what the agent did and why; never counted toward a candidate."""
        refused = self._registered()
        if refused is not None:
            return refused
        mark = "DECISION" if kind == "decision" else "THOUGHT"
        args: dict[str, Any] = {
            "agent_id": self.agent_id,
            "decision": (
                f"{mark}: {what.strip().rstrip('.')}"
                f" because {why.strip().rstrip('.')}."
            ),
            "task_id": self.task["id"] if self.task else "",
        }
        if subject and subject.strip():
            args["subject"] = subject.strip()
        return self.board("log_decision", args)

    def artifact(
        self,
        filename: str,
        content: str,
        description: str = "",
        artifact_type: str = "world-state",
    ) -> dict[str, Any]:
        """Put a file on the board as evidence for the held task."""
        if not self.task:
            return {"success": False, "error": "You hold no task."}
        return self.board(
            "log_artifact",
            {
                "task_id": self.task["id"],
                "filename": filename,
                "content": content,
                "artifact_type": artifact_type,
                "project_root": self.project_root,
                "description": description,
            },
        )

    def opinions(self, limit: int = 50, kind: str | None = None) -> dict[str, Any]:
        """Read the board's markers."""
        args: dict[str, Any] = {
            "project_id": self.project_id,
            "query_type": "opinions",
            "limit": limit,
        }
        if kind:
            args["kind"] = kind
        return self.board("query_project_history", args)

    def decisions(self, limit: int = 20) -> dict[str, Any]:
        """Read the board's decisions."""
        return self.board(
            "query_project_history",
            {
                "project_id": self.project_id,
                "query_type": "decisions",
                "kind": "decision",
                "limit": limit,
            },
        )

    def status(self) -> dict[str, Any]:
        """Read the project's status."""
        return self.board("get_project_status", {})


__all__: list[str] = ["BoardAgent", "ToolClient", "jsonl_logger", "outcome_of"]
