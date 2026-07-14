"""Design-time verification of the rulebook, before any agent runs.

All rule DFAs run side by side as one product automaton over the alphabet of
declared activities. Port of the JS linter: contradiction, trap, dead rule,
shadowed rule — each with a concrete counterexample trace where one exists.
"""

from __future__ import annotations

from .rulebook import Rulebook


def _explore(rulebook: Rulebook, forbid_perm_viol_of: int | None = None):
    rules = rulebook.rules
    alphabet = rulebook.activity_names
    initial = tuple(r.dfa.initial for r in rules)
    nodes: dict[tuple, dict] = {initial: {"parent": None}}
    queue = [initial]
    while queue:
        cur = queue.pop(0)
        for activity in alphabet:
            nxt = tuple(r.dfa.step(cur[i], activity) for i, r in enumerate(rules))
            if forbid_perm_viol_of is not None:
                j = forbid_perm_viol_of
                if rules[j].dfa.color(nxt[j]) == "PERM_VIOL":
                    continue  # pruned world where that rule is enforced
            if nxt not in nodes:
                nodes[nxt] = {"parent": (cur, activity)}
                queue.append(nxt)
    return nodes


def _colors(rulebook: Rulebook, node: tuple) -> list[str]:
    return [r.dfa.color(node[i]) for i, r in enumerate(rulebook.rules)]


def _is_accepting(rulebook: Rulebook, node: tuple) -> bool:
    return all(c in ("PERM_SAT", "CURR_SAT") for c in _colors(rulebook, node))


def _is_healthy(rulebook: Rulebook, node: tuple) -> bool:
    return all(c != "PERM_VIOL" for c in _colors(rulebook, node))


def _trace_to(nodes: dict, node: tuple) -> list[str]:
    trace = []
    cur = nodes[node]
    while cur["parent"]:
        prev, activity = cur["parent"]
        trace.append(activity)
        cur = nodes[prev]
    return list(reversed(trace))


def _co_accessible(rulebook: Rulebook, nodes: dict) -> set:
    reverse: dict[tuple, set] = {}
    rules = rulebook.rules
    for node in nodes:
        for activity in rulebook.activity_names:
            nxt = tuple(r.dfa.step(node[i], activity) for i, r in enumerate(rules))
            if nxt in nodes:
                reverse.setdefault(nxt, set()).add(node)
    alive = {n for n in nodes if _is_accepting(rulebook, n)}
    queue = list(alive)
    while queue:
        n = queue.pop()
        for pred in reverse.get(n, ()):
            if pred not in alive:
                alive.add(pred)
                queue.append(pred)
    return alive


def lint_rulebook(rulebook: Rulebook) -> dict:
    findings = []
    rules = rulebook.rules
    nodes = _explore(rulebook)
    alive = _co_accessible(rulebook, nodes)
    initial = tuple(r.dfa.initial for r in rules)

    if initial not in alive:
        findings.append(
            {
                "severity": "error",
                "kind": "contradiction",
                "message": (
                    "The rulebook is unsatisfiable: no possible run complies with all "
                    "rules together. Every agent is doomed before its first tool call."
                ),
            }
        )

    trap = None
    for node in nodes:
        if node not in alive and _is_healthy(rulebook, node) and node != initial:
            trace = _trace_to(nodes, node)
            if trap is None or len(trace) < len(trap):
                trap = trace
    if trap is not None and initial in alive:
        findings.append(
            {
                "severity": "error",
                "kind": "trap",
                "message": (
                    "A reachable situation exists where no rule is broken yet, but every "
                    "possible continuation violates something. A seatbelt-enforced agent "
                    "would stall there."
                ),
                "trace": trap,
            }
        )

    for i, rule in enumerate(rules):
        if not any(
            rule.dfa.color(node[i]) in ("CURR_VIOL", "PERM_VIOL") for node in nodes
        ):
            findings.append(
                {
                    "severity": "warning",
                    "kind": "dead-rule",
                    "rule": rule.id,
                    "message": (
                        f"Rule '{rule.id}' can never be violated by any trace over the "
                        "declared activities. It costs attention forever and protects nothing."
                    ),
                }
            )

    seatbelts = [(j, r) for j, r in enumerate(rules) if r.posture == "seatbelt"]
    for i, rule in enumerate(rules):
        if not any(rule.dfa.color(node[i]) == "PERM_VIOL" for node in nodes):
            continue
        for j, other in seatbelts:
            if j == i:
                continue
            restricted = _explore(rulebook, forbid_perm_viol_of=j)
            if not any(rule.dfa.color(node[i]) == "PERM_VIOL" for node in restricted):
                findings.append(
                    {
                        "severity": "warning",
                        "kind": "shadowed-rule",
                        "rule": rule.id,
                        "by": other.id,
                        "message": (
                            f"Rule '{rule.id}' can only be permanently violated on runs where "
                            f"seatbelt rule '{other.id}' was already permanently violated first. "
                            f"With that seatbelt enforced, '{rule.id}' can never trigger — "
                            "was that decided on purpose?"
                        ),
                    }
                )

    return {
        "ok": not any(f["severity"] == "error" for f in findings),
        "productStates": len(nodes),
        "findings": findings,
    }
