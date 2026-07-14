"""Port of the JS engine tests — now exercising real LTLf2DFA/MONA machines.

Passing this suite means the MONA-compiled DFAs are semantically equivalent
to the hand-compiled template automata of the JS prototype.
"""

import pytest

from ltl_harness.engine.dfa import compile_formula
from ltl_harness.engine.formulas import build_formula
from ltl_harness.engine.lint import lint_rulebook
from ltl_harness.engine.monitor import RunMonitor
from ltl_harness.engine.rulebook import (
    candidate_activities,
    parse_rulebook,
    resolve_activity,
)


def run(template, params, trace):
    formula, _ = build_formula(template, params)
    dfa = compile_formula(formula)
    state = dfa.initial
    for activity in trace:
        state = dfa.step(state, activity)
    return dfa.color(state)


def test_existence():
    assert run("Existence", {"a": "decide"}, []) == "CURR_VIOL"
    assert run("Existence", {"a": "decide"}, ["x", "y"]) == "CURR_VIOL"
    assert run("Existence", {"a": "decide"}, ["x", "decide", "y"]) == "PERM_SAT"


def test_absence():
    assert run("Absence", {"a": "ask", "n": 2}, ["ask", "ask"]) == "CURR_SAT"
    assert run("Absence", {"a": "ask", "n": 2}, ["ask", "x", "ask", "ask"]) == "PERM_VIOL"


def test_response():
    assert run("Response", {"a": "refund", "b": "audit"}, ["refund"]) == "CURR_VIOL"
    assert run("Response", {"a": "refund", "b": "audit"}, ["refund", "x", "audit"]) == "CURR_SAT"
    assert run("Response", {"a": "refund", "b": "audit"}, ["refund", "audit", "refund"]) == "CURR_VIOL"


def test_precedence():
    assert run("Precedence", {"a": "verify", "b": "refund"}, ["refund"]) == "PERM_VIOL"
    assert run("Precedence", {"a": "verify", "b": "refund"}, ["refund", "verify"]) == "PERM_VIOL"
    assert run("Precedence", {"a": "verify", "b": "refund"}, ["verify", "refund"]) == "PERM_SAT"


def test_chain_response():
    p = {"a": "refund", "b": "audit"}
    assert run("ChainResponse", p, ["refund", "audit"]) == "CURR_SAT"
    assert run("ChainResponse", p, ["refund", "x"]) == "PERM_VIOL"
    assert run("ChainResponse", p, ["refund", "refund"]) == "PERM_VIOL"
    assert run("ChainResponse", p, ["refund"]) == "CURR_VIOL"


def test_not_succession():
    p = {"a": "escalate", "b": "refund"}
    assert run("NotSuccession", p, ["refund", "escalate"]) == "CURR_SAT"
    assert run("NotSuccession", p, ["escalate", "x", "refund"]) == "PERM_VIOL"


def test_init():
    assert run("Init", {"a": "lookup"}, []) == "CURR_VIOL"
    assert run("Init", {"a": "lookup"}, ["lookup", "x"]) == "PERM_SAT"
    assert run("Init", {"a": "lookup"}, ["x"]) == "PERM_VIOL"


def test_disjunctive_params():
    assert run("Existence", {"a": ["refund", "reject", "escalate"]}, ["reject"]) == "PERM_SAT"


def test_raw_formula_rule():
    dfa = compile_formula("G(deploy -> F(smoke_test))")
    s = dfa.step(dfa.initial, "deploy")
    assert dfa.color(s) == "CURR_VIOL"
    assert dfa.color(dfa.step(s, "smoke_test")) == "CURR_SAT"


RB = {
    "name": "test",
    "activities": {
        "lookup": {"tool": "t_lookup"},
        "verify_ok": {"tool": "t_verify", "result": {"path": "verified", "equals": True}},
        "verify_fail": {"tool": "t_verify", "result": {"path": "verified", "equals": False}},
        "refund": {"tool": "t_refund"},
        "audit": {"tool": "t_audit"},
        "escalate": {"tool": "t_escalate"},
    },
    "rules": [
        {
            "id": "no-refund-before-verify",
            "description": "no refund before verification",
            "template": "Precedence",
            "params": {"a": "verify_ok", "b": "refund"},
            "posture": "seatbelt",
        },
        {
            "id": "decision",
            "description": "eventually decide",
            "template": "Existence",
            "params": {"a": ["refund", "escalate"]},
            "posture": "seatbelt",
        },
    ],
}


@pytest.fixture
def rb():
    return parse_rulebook(RB)


def test_activity_resolution(rb):
    ok = {"content": [{"type": "text", "text": '{"verified": true}'}]}
    fail = {"content": [{"type": "text", "text": '{"verified": false}'}]}
    assert resolve_activity(rb, "t_verify", ok) == "verify_ok"
    assert resolve_activity(rb, "t_verify", fail) == "verify_fail"
    assert resolve_activity(rb, "t_verify", ok["content"]) == "verify_ok"  # bare block list
    assert resolve_activity(rb, "t_refund", {}) == "refund"
    assert resolve_activity(rb, "unmapped", {}) is None
    assert candidate_activities(rb, "t_verify") == ["verify_ok", "verify_fail"]


def test_monitor_decisions(rb):
    mon = RunMonitor(rb)
    assert mon.decide(candidate_activities(rb, "t_refund"))["decision"] == "deny"
    assert mon.decide(candidate_activities(rb, "t_verify"))["decision"] == "allow"
    mon.advance("verify_ok")
    assert mon.decide(candidate_activities(rb, "t_refund"))["decision"] == "allow"


def test_finalize(rb):
    mon = RunMonitor(rb)
    mon.advance("lookup")
    assert mon.finalize()["decision"]["verdict"] == "VIOLATED"
    mon.advance("escalate")
    assert mon.finalize()["decision"]["verdict"] == "SATISFIED"


def test_lint_healthy(rb):
    result = lint_rulebook(rb)
    assert result["ok"] is True
    assert result["findings"] == []


def test_lint_contradiction():
    rb = parse_rulebook(
        {
            "name": "broken",
            "activities": {"refund": {"tool": "t_r"}, "lookup": {"tool": "t_l"}},
            "rules": [
                {"id": "must-refund", "template": "Existence", "params": {"a": "refund"}, "posture": "seatbelt"},
                {"id": "never-refund", "template": "Absence", "params": {"a": "refund", "n": 0}, "posture": "seatbelt"},
            ],
        }
    )
    result = lint_rulebook(rb)
    assert result["ok"] is False
    assert any(f["kind"] == "contradiction" for f in result["findings"])


def test_lint_trap():
    rb = parse_rulebook(
        {
            "name": "trappy",
            "activities": {
                "escalate": {"tool": "t_e"},
                "refund": {"tool": "t_r"},
                "lookup": {"tool": "t_l"},
            },
            "rules": [
                {"id": "must-refund", "template": "Existence", "params": {"a": "refund"}, "posture": "seatbelt"},
                {"id": "no-refund-after-escalate", "template": "NotSuccession",
                 "params": {"a": "escalate", "b": "refund"}, "posture": "seatbelt"},
            ],
        }
    )
    trap = next(f for f in lint_rulebook(rb)["findings"] if f["kind"] == "trap")
    assert trap["trace"] == ["escalate"]


def test_lint_shadowed():
    rb = parse_rulebook(
        {
            "name": "shadowy",
            "activities": {"verify": {"tool": "t_v"}, "refund": {"tool": "t_r"}, "lookup": {"tool": "t_l"}},
            "rules": [
                {"id": "strict", "template": "Absence", "params": {"a": "refund", "n": 0}, "posture": "seatbelt"},
                {"id": "weaker", "template": "Precedence", "params": {"a": "verify", "b": "refund"}, "posture": "recorder"},
            ],
        }
    )
    shadow = next(
        f for f in lint_rulebook(rb)["findings"] if f["kind"] == "shadowed-rule" and f["rule"] == "weaker"
    )
    assert shadow["by"] == "strict"


def test_machine_json_shape(rb):
    machines = rb.machines_json()
    m = machines[0]["machine"]
    assert {"template", "formula", "english", "states", "initial", "edges", "otherEdges"} <= set(m)
    assert all({"id", "label", "color"} <= set(s) for s in m["states"])
    ids = {s["id"] for s in m["states"]}
    assert m["initial"] in ids
    assert all(e["from"] in ids and e["to"] in ids and e["on"] for e in m["edges"])
