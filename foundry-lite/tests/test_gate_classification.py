"""Tests for the shared gate-kind classification seam (foundry-dao.4).

Covers: config.classify_gate as the sole place gate.run/gate.act/gate.dagger
and run-tool prefixes are inspected. Regression coverage for the
task/mise prefix drift bug — doctor.py used to match bare "task"/"mise"
while runner.py required the space-terminated prefixes "task " / "mise run ",
so doctor could pass a gate that runner would dispatch as a raw shell command.
"""
from foundry.config import GATE_KIND_FIELDS, ActConfig, DaggerBlock, GateConfig, classify_gate, validate_config
from foundry.runner import _detect_runner, detect_runner


def _gate(run=None, act=None, dagger=None):
    return GateConfig(id="g", run=run, act=act, dagger=dagger)


class TestClassifyGate:
    def test_act_gate(self):
        assert classify_gate(_gate(act=ActConfig(workflow="ci.yml"))) == "act"

    def test_dagger_gate(self):
        assert classify_gate(_gate(dagger=DaggerBlock(module="./ci", function="test"))) == "dagger"

    def test_make_gate(self):
        assert classify_gate(_gate(run="make test")) == "make"

    def test_just_gate(self):
        assert classify_gate(_gate(run="just test")) == "just"

    def test_task_gate(self):
        assert classify_gate(_gate(run="task test")) == "task"

    def test_mise_gate(self):
        assert classify_gate(_gate(run="mise run test")) == "mise"

    def test_shell_gate(self):
        assert classify_gate(_gate(run="echo hi")) == "shell"

    def test_accepts_raw_dict(self):
        assert classify_gate({"run": "task test"}) == "task"
        assert classify_gate({"act": {"workflow": "ci.yml"}}) == "act"
        assert classify_gate({"dagger": {"module": "./ci", "function": "test"}}) == "dagger"


class TestTaskMiseConsistencyBetweenDoctorAndRunner:
    """A gate with run: "mise run test" or "task test" must be classified
    identically by doctor.py and runner.py — this failed before the fix,
    since doctor.py matched bare "task"/"mise" while runner.py required
    the space-terminated prefixes."""

    def test_mise_run_test_classified_identically(self, tmp_path):
        gate = _gate(run="mise run test")
        runner_kind, _ = detect_runner(gate.run)
        assert runner_kind == "mise"
        assert classify_gate(gate) == runner_kind

    def test_task_test_classified_identically(self, tmp_path):
        gate = _gate(run="task test")
        runner_kind, _ = detect_runner(gate.run)
        assert runner_kind == "task"
        assert classify_gate(gate) == runner_kind

    def test_doctor_taskfile_check_uses_same_classification_as_runner(self, tmp_path, capsys):
        """Regression: a bare-prefix gate like run: "taskmaster-report" must
        NOT be misclassified as a task gate by either doctor or runner now
        that both share one prefix table requiring a trailing space."""
        gate = _gate(run="taskmaster-report")
        assert classify_gate(gate) == "shell"
        runner_kind, _ = detect_runner(gate.run)
        assert runner_kind == "shell"


class TestDetectRunnerUsesSharedTable:
    def test_detect_runner_make(self):
        assert detect_runner("make test") == ("make", "make test")

    def test_detect_runner_just(self):
        assert detect_runner("just test") == ("just", "just test")

    def test_detect_runner_task(self):
        assert detect_runner("task test") == ("task", "task test")

    def test_detect_runner_mise(self):
        assert detect_runner("mise run test") == ("mise", "mise run test")

    def test_detect_runner_shell_fallback(self):
        assert detect_runner("echo hi") == ("shell", 'sh -c "echo hi"')


class TestUnderscoreDetectRunnerUsesClassifyGate:
    def test_act_gate_routes_through_classify_gate(self):
        gate = _gate(act=ActConfig(workflow="ci.yml", event="push"))
        runner_type, cmd = _detect_runner(gate)
        assert runner_type == "act"
        assert cmd == "act push -W ci.yml"

    def test_dagger_gate_routes_through_classify_gate(self):
        gate = _gate(dagger=DaggerBlock(module="./ci", function="test"))
        runner_type, cmd = _detect_runner(gate)
        assert runner_type == "dagger"
        assert cmd == "dagger call -m ./ci test"


class TestValidateConfigInvariantPreserved:
    def _profile(self, gate: dict) -> dict:
        return {"version": 1, "profiles": {"p": {"gates": [{"id": "g", **gate}]}}}

    def test_zero_of_run_act_dagger_fails(self):
        errors = validate_config(self._profile({}))
        assert any("exactly one of" in e for e in errors)

    def test_two_of_run_act_dagger_fails(self):
        errors = validate_config(self._profile({"run": "echo hi", "act": {"workflow": "ci.yml"}}))
        assert any("exactly one of" in e for e in errors)

    def test_exactly_one_passes(self):
        errors = validate_config(self._profile({"run": "echo hi"}))
        assert errors == []

    def test_gate_kind_fields_matches_validate_config_fields(self):
        assert set(GATE_KIND_FIELDS) == {"run", "act", "dagger"}
