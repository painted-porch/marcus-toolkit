"""
Seed a project with exactly the tasks an extension names, through MCP.

``create_project(options.tasks)`` with ``options.path = "direct"`` puts the
listed tasks on the board and runs no model decomposition. The two traps the
Minecraft runs found are enforced here: the criteria key is
``completion_criteria``, ``depends_on`` names tasks by name, and an extra key
on a spec breaks the call (six of seven promotions in Minecraft run 6).

No label from the ``build`` family goes on any task: those labels switch on
the software validation gate, which rejects a task with no source files
(ADR-0013 K3, issue #798).

Every seeded project also carries one extra done card, ``About: <name>``,
which Marcus creates itself; counts read back from the board are seeded + 1.

Since Marcus ``b33dad5f`` (ADR-0013 K2, the MCP-only write path, #911) a spec
may also carry ``task_type`` (which kernel rules the card falls under),
``inputs`` (what the board hands the worker that pulls it) and ``id`` (the
card's final id, ``[A-Za-z0-9_-]``), ``depends_on`` may name a task by that
id, and the project's task-type declaration (``task_type_registrations``,
K2) rides in the same ``create_project`` call. The rules enforced here are
the kernel's own, applied before the call so the refusal is immediate and
local.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import MarcusMCPClient

#: The keys a task spec may carry on ``create_project`` and ``create_tasks``.
TASK_KEYS = frozenset(
    {
        "name",
        "description",
        "labels",
        "depends_on",
        "completion_criteria",
        "estimated_hours",
        "priority",
        # Marcus b33dad5f (#911): the typed-chain fields.
        "task_type",
        "inputs",
        "id",
    }
)
#: The characters a caller-supplied ``id`` may use (the board's own rule).
ID_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")
#: Labels that switch on the software validation gate (K3); never on a board-only task.
FORBIDDEN_LABELS = frozenset({"build", "create", "implement", "develop", "feature"})
PRIORITIES = frozenset({"high", "medium", "low"})


@dataclass
class SeedResult:
    """What seeding produced."""

    project_id: str
    task_names: list[str]
    total_tasks: int | None
    reply: dict[str, Any] = field(default_factory=dict)


def validate_specs(specs: Sequence[dict[str, Any]]) -> None:
    """
    Refuse a task list the board would mis-handle.

    Parameters
    ----------
    specs : Sequence[Dict[str, Any]]
        Task specs.

    Raises
    ------
    ValueError
        On an unknown key, a forbidden label, a bad priority, a duplicate
        name, or a ``depends_on`` that names no task in the list.
    """
    names = [str(s.get("name") or "") for s in specs]
    if len(set(names)) != len(names) or any(not n for n in names):
        raise ValueError("every task needs a distinct, non-empty name")
    ids = [str(s["id"]) for s in specs if s.get("id")]
    if len(set(ids)) != len(ids):
        raise ValueError("every caller-supplied id must be distinct")
    for spec in specs:
        unknown = set(spec) - TASK_KEYS
        if unknown:
            raise ValueError(
                f"task {spec['name']!r}: unknown keys {sorted(unknown)};"
                f" allowed {sorted(TASK_KEYS)}"
            )
        bad = {
            str(label).lower() for label in spec.get("labels") or []
        } & FORBIDDEN_LABELS
        if bad:
            raise ValueError(
                f"task {spec['name']!r}: label(s) {sorted(bad)} switch on the"
                " software validation gate (K3)"
            )
        if "priority" in spec and spec["priority"] not in PRIORITIES:
            raise ValueError(
                f"task {spec['name']!r}: priority must be one of {sorted(PRIORITIES)}"
            )
        for dep in spec.get("depends_on") or []:
            if dep not in names and dep not in ids:
                raise ValueError(
                    f"task {spec['name']!r}: depends_on {dep!r} names no task"
                    " in this list, by name or by id"
                )
        if "task_type" in spec and (
            not isinstance(spec["task_type"], str) or not spec["task_type"].strip()
        ):
            raise ValueError(
                f"task {spec['name']!r}: task_type must be a non-empty string"
            )
        if "inputs" in spec and not isinstance(spec["inputs"], Mapping):
            raise ValueError(f"task {spec['name']!r}: inputs must be a mapping")
        if "id" in spec and (
            not isinstance(spec["id"], str) or not ID_CHARSET.match(spec["id"])
        ):
            raise ValueError(
                f"task {spec['name']!r}: id must be a string over [A-Za-z0-9_-]"
            )
        criteria = spec.get("completion_criteria")
        if criteria is not None and not isinstance(criteria, list):
            raise ValueError(
                f"task {spec['name']!r}: completion_criteria must be a list"
                " of sentences"
            )


def validate_task_type_registrations(declaration: Any) -> None:
    """
    Refuse a task-type declaration the kernel would refuse (ADR-0013 K2).

    Parameters
    ----------
    declaration : Any
        ``options.task_type_registrations`` as it would be sent: a mapping of
        non-empty task-type names to mappings. An entry may carry
        ``evidence_schema``, ``verifies_task_type``, ``checks_injectable``,
        ``injected_checks`` (only with ``checks_injectable`` true) and
        ``requires_source_tree``.

    Raises
    ------
    ValueError
        Not a mapping, an empty type name, an entry that is not a mapping, or
        ``injected_checks`` without ``checks_injectable`` set to true.
    """
    if not isinstance(declaration, Mapping):
        raise ValueError(
            "task_type_registrations must be a mapping of task type to registration"
        )
    for task_type, registration in declaration.items():
        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("every task type name must be a non-empty string")
        if not isinstance(registration, Mapping):
            raise ValueError(f"task type {task_type!r}: registration must be a mapping")
        if registration.get("injected_checks") and (
            registration.get("checks_injectable") is not True
        ):
            raise ValueError(
                f"task type {task_type!r}: injected_checks needs checks_injectable"
                " set to true (ADR-0013 K4)"
            )


def seed_project(
    client: MarcusMCPClient,
    *,
    name: str,
    description: str,
    tasks: Sequence[dict[str, Any]],
    project_root: Path,
    objective: str | None = None,
    task_type_registrations: Mapping[str, Mapping[str, Any]] | None = None,
    wait_seconds: float = 120.0,
    sleep_seconds: float = 2.0,
) -> SeedResult:
    """
    Create a project holding exactly ``tasks``, and read back that it did.

    Parameters
    ----------
    client : MarcusMCPClient
        A connected client.
    name : str
        The project name. Never contains "test".
    description : str
        The project description.
    tasks : Sequence[Dict[str, Any]]
        Task specs, validated by :func:`validate_specs`.
    project_root : Path
        Where artifacts are written; the kernel refuses a project without one
        (``project_root_required``). Created if missing, resolved to an absolute
        path, and deliberately not a repository.
    objective : Optional[str]
        A human-written objective every agent reads once at registration.
    task_type_registrations : Optional[Mapping[str, Mapping[str, Any]]]
        The project's ADR-0013 K2 task-type declaration, stamped on the
        project record by the same call (Marcus b33dad5f, #911); validated
        by :func:`validate_task_type_registrations`.
    wait_seconds : float
        How long to wait for the board to show the tasks.
    sleep_seconds : float
        The poll interval while waiting.

    Returns
    -------
    SeedResult

    Raises
    ------
    ValueError
        On a bad task list, a bad declaration, or a project name containing
        "test".
    RuntimeError
        When the server returns no project id.
    """
    if "test" in name.lower():
        raise ValueError(
            "a project name containing 'test' changes Marcus's behaviour;"
            " pick another name"
        )
    validate_specs(tasks)
    if task_type_registrations is not None:
        validate_task_type_registrations(task_type_registrations)
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "provider": "sqlite",
        "complexity": "prototype",
        "path": "direct",
        "project_root": str(root),
        # A spec's inputs may arrive as any mapping (validation accepts
        # one); the wire wants plain dicts (Codex P2 on PR #2).
        "tasks": [
            {**spec, "inputs": dict(spec["inputs"])} if "inputs" in spec else dict(spec)
            for spec in tasks
        ],
    }
    if objective:
        options["objective"] = objective
    if task_type_registrations is not None:
        options["task_type_registrations"] = {
            k: dict(v) for k, v in task_type_registrations.items()
        }
    reply = client.call(
        "create_project",
        {"description": description, "project_name": name, "options": options},
    )
    project_id = reply.get("project_id")
    if not project_id:
        raise RuntimeError(f"create_project returned no project id: {reply}")
    total: int | None = None
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        status = (client.call_tool("get_project_status") or {}).get("project") or {}
        if (status.get("total_tasks") or 0) >= len(tasks):
            total = int(status["total_tasks"])
            break
        time.sleep(sleep_seconds)
    return SeedResult(
        project_id=str(project_id),
        task_names=[str(t["name"]) for t in tasks],
        total_tasks=total,
        reply=reply,
    )
