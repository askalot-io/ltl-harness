"""Lint the rulebook before the flight. Run in CI on every edit.

    python -m ltl_harness.lint_cli ../rules/refund.rules.yaml

Exit code 1 on errors (contradiction / trap), 0 otherwise.
"""

import sys

from .engine.lint import lint_rulebook
from .engine.rulebook import load_rulebook


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m ltl_harness.lint_cli <rulebook.yaml>", file=sys.stderr)
        return 2
    try:
        rulebook = load_rulebook(sys.argv[1])
    except Exception as err:  # noqa: BLE001 — CLI boundary
        print(f"✗ rulebook does not parse/compile: {err}")
        return 1

    result = lint_rulebook(rulebook)
    print(
        f"Rulebook '{rulebook.name}' — {len(rulebook.rules)} rules, "
        f"{len(rulebook.activity_names)} activities, "
        f"{result['productStates']} product states explored"
    )
    for rule in rulebook.rules:
        print(f"  {rule.id}: {rule.formula}  [{len(rule.dfa.states)} DFA states]")

    if not result["findings"]:
        print("✓ no contradictions, traps, dead rules, or shadowed rules")
    for f in result["findings"]:
        icon = "✗" if f["severity"] == "error" else "⚠"
        print(f"{icon} [{f['kind']}] {f['message']}")
        if f.get("trace") is not None:
            print(f"    counterexample trace: {' → '.join(f['trace']) or '(empty trace)'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
