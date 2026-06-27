from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ActConfig:
    workflow: str
    event: str = "push"


@dataclass
class DaggerBlock:
    module: str
    function: str
    args: list[str] = field(default_factory=list)


@dataclass
class GateConfig:
    id: str
    run: str | None = None
    act: ActConfig | None = None
    dagger: DaggerBlock | None = None
    timeout: str = "10m"
    allow_failure: bool = False
    decision_on_failure: str = "fail"


@dataclass
class ProfileConfig:
    gates: list[GateConfig]
    parallel: bool = False


@dataclass
class ScheduleConfig:
    profile: str
    cron: str


@dataclass
class FoundryConfig:
    version: int
    profiles: dict[str, ProfileConfig]
    schedules: dict[str, ScheduleConfig] = field(default_factory=dict)
    integrations: dict = field(default_factory=dict)
    # integrations.agent.approval_required: bool (default False)
    # When True:  on failure, write next_action with status=pending_approval
    #             and do NOT fire integrations.agent.command automatically.
    # When False: existing auto-fire behavior unchanged.


def load_config(path: str = "foundry.yaml") -> FoundryConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} not found. Run foundry doctor.")
    try:
        with open(p) as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"{path} invalid YAML: {e}")
    errors = validate_config(cfg)
    if errors:
        raise ValueError("foundry.yaml validation errors:\n" + "\n".join(f"  - {e}" for e in errors))
    return _build(cfg)


def _build(cfg: dict) -> FoundryConfig:
    profiles = {}
    for name, pdata in cfg.get("profiles", {}).items():
        gates = []
        for g in pdata.get("gates", []):
            act_raw = g.get("act")
            act = ActConfig(workflow=act_raw["workflow"], event=act_raw.get("event", "push")) if act_raw else None
            dagger_raw = g.get("dagger")
            dagger = DaggerBlock(
                module=dagger_raw["module"],
                function=dagger_raw["function"],
                args=dagger_raw.get("args", []),
            ) if dagger_raw else None
            gates.append(GateConfig(
                id=g["id"],
                run=g.get("run"),
                act=act,
                dagger=dagger,
                timeout=g.get("timeout", "10m"),
                allow_failure=g.get("allow_failure", False),
                decision_on_failure=g.get("decision_on_failure", "fail"),
            ))
        profiles[name] = ProfileConfig(gates=gates, parallel=pdata.get("parallel", False))

    schedules = {}
    for name, sdata in cfg.get("schedules", {}).items():
        schedules[name] = ScheduleConfig(profile=sdata["profile"], cron=sdata["cron"])

    return FoundryConfig(version=cfg["version"], profiles=profiles, schedules=schedules,
                         integrations=cfg.get("integrations", {}))


def validate_config(cfg: dict) -> list[str]:
    errors = []
    if cfg.get("version") != 1:
        errors.append("version must be 1")
    for name, profile in cfg.get("profiles", {}).items():
        for gate in profile.get("gates", []):
            if not gate.get("id"):
                errors.append(f"profile '{name}' has gate missing 'id'")
            if sum(["run" in gate, "act" in gate, "dagger" in gate]) != 1:
                errors.append(f"profile '{name}' gate '{gate.get('id')}' must have exactly one of 'run', 'act', or 'dagger'")
            if gate.get("decision_on_failure", "fail") == "warn" and not gate.get("allow_failure", False):
                errors.append(f"gate '{gate.get('id')}': decision_on_failure='warn' requires allow_failure=true")
    return errors


_TIMEOUT_RE = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)$")

def parse_timeout(timeout_str: str) -> float:
    m = _TIMEOUT_RE.match(timeout_str.strip())
    if not m:
        raise ValueError(f"Invalid timeout format: {timeout_str!r}. Expected e.g. '30s', '5m', '1h'.")
    value, unit = float(m.group(1)), m.group(2)
    return value * {"s": 1, "m": 60, "h": 3600}[unit]
