"""Rulebook loading + the world-to-symbols translation (Python port).

Same YAML format as the JS version — plus one addition: a rule may carry a
raw LTLf `formula` instead of a template. Every rule, template or raw, is
compiled to a colored DFA via LTLf2DFA/MONA.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import yaml

from .dfa import ColoredDFA, compile_formula
from .formulas import build_formula

POSTURES = ("recorder", "tripwire", "seatbelt")


@dataclass
class Rule:
    id: str
    description: str
    template: str          # template name, or "LTLf" for raw-formula rules
    params: dict
    posture: str
    formula: str
    english: str
    dfa: ColoredDFA


@dataclass
class Rulebook:
    name: str
    version: int | None
    source_path: str
    activities: dict
    activity_names: list[str]
    rules: list[Rule] = field(default_factory=list)

    def rule(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)

    def machines_json(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "description": r.description,
                "template": r.template,
                "params": r.params,
                "posture": r.posture,
                "machine": r.dfa.to_machine_json(self.activity_names, r.template, r.english),
            }
            for r in self.rules
        ]


def load_rulebook(path: str) -> Rulebook:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return parse_rulebook(raw, path)


def parse_rulebook(raw: dict, source_path: str = "<inline>") -> Rulebook:
    if not isinstance(raw, dict):
        raise ValueError(f"Rulebook {source_path}: empty or not a mapping")
    activities = raw.get("activities") or {}
    if not activities:
        raise ValueError(f"Rulebook {source_path}: no activities defined")
    for name, spec in activities.items():
        if not isinstance(spec, dict) or "tool" not in spec:
            raise ValueError(f"Activity '{name}': missing 'tool'")
        cond = spec.get("result")
        if cond is not None and ("path" not in cond or "equals" not in cond):
            raise ValueError(f"Activity '{name}': result condition needs 'path' and 'equals'")

    activity_names = list(activities)
    rulebook = Rulebook(
        name=raw.get("name", source_path),
        version=raw.get("version"),
        source_path=source_path,
        activities=activities,
        activity_names=activity_names,
    )

    seen_ids = set()
    for r in raw.get("rules") or []:
        rid = r.get("id")
        if not rid:
            raise ValueError(f"Rulebook {source_path}: rule without id")
        if rid in seen_ids:
            raise ValueError(f"Duplicate rule id '{rid}'")
        seen_ids.add(rid)
        posture = r.get("posture", "recorder")
        if posture not in POSTURES:
            raise ValueError(f"Rule '{rid}': unknown posture '{posture}'")

        params = r.get("params") or {}
        for key in ("a", "b"):
            v = params.get(key)
            if v is None:
                continue
            for act in v if isinstance(v, list) else [v]:
                if act not in activity_names:
                    raise ValueError(f"Rule '{rid}': param {key} references unknown activity '{act}'")

        if "formula" in r:
            formula, english = r["formula"], r.get("description", r["formula"])
            template = "LTLf"
        else:
            formula, english = build_formula(r["template"], params)
            template = r["template"]

        dfa = compile_formula(formula)
        for var in dfa.variables:
            if var not in activity_names:
                raise ValueError(f"Rule '{rid}': formula symbol '{var}' is not a declared activity")

        rulebook.rules.append(
            Rule(
                id=rid,
                description=r.get("description", ""),
                template=template,
                params=params,
                posture=posture,
                formula=formula,
                english=english,
                dfa=dfa,
            )
        )
    return rulebook


# ------------------------------------------------------------------ #
# World -> symbols                                                    #
# ------------------------------------------------------------------ #

def _get_path(obj, path: str):
    cur = obj
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def extract_result(tool_response):
    """Pull a comparable result object out of a tool_response.

    MCP results arrive either as {content: [blocks], ...} or as the bare
    block list; the payload of our tools is JSON in the first text block.
    """
    if not isinstance(tool_response, (dict, list)):
        return tool_response
    if isinstance(tool_response, dict) and tool_response.get("structuredContent"):
        return tool_response["structuredContent"]
    blocks = tool_response if isinstance(tool_response, list) else tool_response.get("content")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block.get("text", ""))
                except (json.JSONDecodeError, TypeError):
                    break
    return tool_response


def candidate_activities(rulebook: Rulebook, tool_name: str) -> list[str]:
    """PreToolUse: every activity this call COULD resolve to (result unknown)."""
    return [n for n in rulebook.activity_names if rulebook.activities[n]["tool"] == tool_name]


def resolve_activity(rulebook: Rulebook, tool_name: str, tool_response) -> str | None:
    """PostToolUse: the actual activity, from tool name + result."""
    candidates = candidate_activities(rulebook, tool_name)
    if not candidates:
        return None
    result = extract_result(tool_response)
    for name in candidates:
        cond = rulebook.activities[name].get("result")
        if cond and _get_path(result, cond["path"]) == cond["equals"]:
            return name
    return next(
        (n for n in candidates if not rulebook.activities[n].get("result")), None
    )
