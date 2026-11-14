"""Unit tests for marcus_toolkit.seed."""

import pytest

from marcus_toolkit import seed

pytestmark = pytest.mark.unit

GOOD = [
    {
        "name": "Hold the north ford",
        "description": "d",
        "labels": ["war", "seeded"],
        "completion_criteria": ["c"],
        "estimated_hours": 0.2,
        "priority": "high",
    },
    {
        "name": "Cross the river",
        "description": "d",
        "labels": ["war"],
        "depends_on": ["Hold the north ford"],
        "priority": "medium",
    },
]


class FakeClient:
    """Records calls and answers like the server."""

    def __init__(self, project_id="p1", total=3):
        self.calls = []
        self.project_id = project_id
        self.total = total

    def call(self, name, arguments=None):
        self.calls.append((name, arguments))
        return {
            "success": True,
            "project_id": self.project_id,
            "actual_decomposer": "explicit",
        }

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return {"project": {"total_tasks": self.total}}


def test_validate_accepts_a_good_list():
    """Names, labels, criteria and a dependency by name."""
    seed.validate_specs(GOOD)


@pytest.mark.parametrize(
    "bad, message",
    [
        ([{"name": "a", "labels": ["build"]}], "software validation gate"),
        ([{"name": "a", "acceptance_criteria": ["x"]}], "unknown keys"),
        ([{"name": "a", "depends_on": ["b"]}], "names no task"),
        ([{"name": "a"}, {"name": "a"}], "distinct"),
        ([{"name": "a", "priority": "urgent"}], "priority"),
        ([{"name": "a", "completion_criteria": "one string"}], "list of sentences"),
    ],
)
def test_validate_refuses_what_the_board_mishandles(bad, message):
    """Each trap the Minecraft runs found is refused before the call."""
    with pytest.raises(ValueError, match=message):
        seed.validate_specs(bad)


def test_seed_project_sends_the_direct_path_and_reads_back(monkeypatch, tmp_path):
    """options.path is 'direct', the tasks go as given, the board is read back."""
    monkeypatch.setattr(seed.time, "sleep", lambda s: None)
    client = FakeClient(total=3)
    result = seed.seed_project(
        client,
        name="war-blue",
        description="blue's board",
        tasks=GOOD,
        project_root=tmp_path / "world",
        objective="Hold the line",
    )
    name, args = client.calls[0]
    assert name == "create_project"
    assert args["project_name"] == "war-blue"
    assert args["options"]["path"] == "direct"
    assert args["options"]["provider"] == "sqlite"
    assert args["options"]["tasks"] == GOOD
    assert args["options"]["objective"] == "Hold the line"
    assert args["options"]["project_root"] == str((tmp_path / "world").resolve())
    assert (tmp_path / "world").is_dir()
    assert result.project_id == "p1"
    assert result.total_tasks == 3
    assert result.task_names == ["Hold the north ford", "Cross the river"]


def test_seed_project_refuses_test_in_the_name_and_a_missing_project_id(
    monkeypatch, tmp_path
):
    """'test' in a name changes Marcus's behaviour; no id means nothing seeded."""
    with pytest.raises(ValueError, match="test"):
        seed.seed_project(
            FakeClient(),
            name="war-test",
            description="d",
            tasks=GOOD,
            project_root=tmp_path,
        )
    monkeypatch.setattr(seed.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="no project id"):
        seed.seed_project(
            FakeClient(project_id=""),
            name="war-red",
            description="d",
            tasks=GOOD,
            project_root=tmp_path,
        )


TYPED = [
    {
        "name": "Find the source of case-1",
        "id": "case-1-find",
        "task_type": "find",
        "inputs": {"case_id": "case-1", "query": "who said it"},
        "labels": ["chain"],
    },
    {
        "name": "Verify the claim for case-1",
        "id": "case-1-verify",
        "task_type": "verify",
        "inputs": {"case_id": "case-1"},
        "depends_on": ["case-1-find"],
        "labels": ["chain"],
    },
]

DECLARATION = {
    "find": {"evidence_schema": {"required": ["headline", "url"]}},
    "verify": {
        "evidence_schema": {"required": ["verdict", "checks"]},
        "verifies_task_type": "find",
    },
}


def test_validate_accepts_the_typed_fields_and_a_dependency_by_id():
    """Marcus b33dad5f (#911): task_type, inputs and id travel; depends_on may name an id."""
    seed.validate_specs(TYPED)


@pytest.mark.parametrize(
    "bad, message",
    [
        ([{"name": "a", "task_type": ""}], "task_type"),
        ([{"name": "a", "task_type": 3}], "task_type"),
        ([{"name": "a", "inputs": ["not", "a", "mapping"]}], "inputs"),
        ([{"name": "a", "id": "has spaces"}], "A-Za-z0-9_-"),
        ([{"name": "a", "id": "dup"}, {"name": "b", "id": "dup"}], "distinct"),
    ],
)
def test_validate_refuses_malformed_typed_fields(bad, message):
    """What the kernel's validator would refuse is refused here first."""
    with pytest.raises(ValueError, match=message):
        seed.validate_specs(bad)


def test_validate_task_type_registrations_accepts_a_declaration():
    """A mapping of task type to mapping is the whole shape rule."""
    seed.validate_task_type_registrations(DECLARATION)


@pytest.mark.parametrize(
    "bad, message",
    [
        (["find"], "mapping"),
        ({"": {}}, "non-empty"),
        ({"find": "evidence please"}, "mapping"),
        ({"verify": {"injected_checks": [{"name": "x"}]}}, "checks_injectable"),
    ],
)
def test_validate_task_type_registrations_refuses_what_the_kernel_would(bad, message):
    """The kernel's own rules, applied before the call."""
    with pytest.raises(ValueError, match=message):
        seed.validate_task_type_registrations(bad)


def test_seed_project_sends_the_declaration_with_the_typed_cards(monkeypatch, tmp_path):
    """options.task_type_registrations rides in the same create_project call."""
    monkeypatch.setattr(seed.time, "sleep", lambda s: None)
    client = FakeClient(total=3)
    seed.seed_project(
        client,
        name="chain-blue",
        description="a typed chain",
        tasks=TYPED,
        project_root=tmp_path / "root",
        task_type_registrations=DECLARATION,
    )
    _, args = client.calls[0]
    assert args["options"]["tasks"] == TYPED
    assert args["options"]["task_type_registrations"] == DECLARATION


def test_seed_project_without_a_declaration_sends_none(monkeypatch, tmp_path):
    """No declaration: the option is absent, exactly as before."""
    monkeypatch.setattr(seed.time, "sleep", lambda s: None)
    client = FakeClient(total=3)
    seed.seed_project(
        client,
        name="chain-red",
        description="d",
        tasks=GOOD,
        project_root=tmp_path / "root",
    )
    assert "task_type_registrations" not in client.calls[0][1]["options"]
