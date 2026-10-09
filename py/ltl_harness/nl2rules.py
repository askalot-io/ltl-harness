"""Design time: natural language -> DECLARE pattern -> LTLf rule, with the
round-trip check that keeps the translation honest.

Needs the `nl` extra (nl2ltl), which the runtime engine never imports:

    .venv/bin/python -m ltl_harness.nl2rules "Every refund must eventually be audited." \
        --id audit-eventually --posture recorder [--yes] [--dry-run]

Pipeline (the role reversal from the article):
  1. a Claude-backed nl2ltl Engine translates the policy sentence into a
     DECLARE pattern grounded in the rulebook's activity vocabulary
     (creative component, design time, human-reviewed);
  2. the pattern is rendered to an LTLf formula (nl2ltl/pylogics);
  3. Claude paraphrases the FORMULA back to English without seeing the
     original sentence — you compare the two (round trip);
  4. on approval the rule is appended to the rulebook as a raw-formula rule
     and the runtime linter re-verifies the whole rulebook (the
     deterministic component). If the lint fails, the append is reverted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict

from claude_agent_sdk import ClaudeAgentOptions, query
from nl2ltl import translate
from nl2ltl.declare import declare as declare_patterns
from nl2ltl.declare.declare import TemplateEnum
from nl2ltl.engines.base import Engine
from nl2ltl.filters.base import Filter
from pylogics.syntax.base import Formula
from pylogics.syntax.ltl import Atomic
from pylogics.utils.to_string import to_string

from .paths import EXAMPLE_RULEBOOK, IN_CHECKOUT, RULEBOOK_PATH, read_dotenv

PATTERN_CATALOG = ", ".join(t.value for t in TemplateEnum)


def _load_env() -> None:
    dotenv = read_dotenv()
    if dotenv is None:
        return
    os.environ.pop("ANTHROPIC_API_KEY", None)
    for key, value in dotenv.items():
        os.environ.setdefault(key, value)


async def _ask_claude(system_prompt: str, prompt: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt, model="claude-sonnet-5", tools=[], max_turns=1
    )
    text = ""
    async for message in query(prompt=prompt, options=options):
        if type(message).__name__ == "AssistantMessage":
            for block in message.content:
                if type(block).__name__ == "TextBlock":
                    text += block.text
    return text.strip()


class ClaudeEngine(Engine):
    """An nl2ltl Engine that uses Claude for the NL -> DECLARE step.

    Speaks the same PATTERN/SYMBOLS contract as nl2ltl's GPT engine and
    reuses nl2ltl's own grounding to build the pattern objects.
    """

    def __init__(self, activities: list[str]):
        self.activities = activities

    def translate(self, utterance: str, filtering: Filter = None) -> Dict[Formula, float]:
        system_prompt = (
            "You translate one business-policy sentence into exactly one DECLARE "
            f"pattern. Patterns: {PATTERN_CATALOG}. "
            f"Symbols MUST come from this vocabulary: {', '.join(self.activities)}. "
            "Unary patterns (Existence, ExistenceTwo, Absence) take one symbol; the "
            "others take two symbols in template order (e.g. Response(a, b) = after a, "
            "eventually b; Precedence(a, b) = b only after a). Reply with exactly two "
            "lines and nothing else:\nPATTERN: <PatternName>\nSYMBOLS: <sym1>[, <sym2>]"
        )
        answer = asyncio.run(_ask_claude(system_prompt, utterance))
        pattern_m = re.search(r"PATTERN:\s*(\w+)", answer)
        symbols_m = re.search(r"SYMBOLS:\s*(.+)", answer)
        if not pattern_m or not symbols_m:
            raise ValueError(f"Claude reply did not match the contract:\n{answer}")
        symbols = [s.strip() for s in symbols_m.group(1).split(",") if s.strip()]
        unknown = [s for s in symbols if s not in self.activities]
        if unknown:
            raise ValueError(f"Claude used symbols outside the vocabulary: {unknown}")

        # Construct the pattern directly, preserving Claude's operand order.
        # (nl2ltl's own grounding has an operand-duplication bug in
        # ground_notcoexistence and ignores symbol order elsewhere.)
        names = {t.value.lower(): t.value for t in TemplateEnum}
        cls_name = names.get(pattern_m.group(1).lower())
        if cls_name is None:
            raise ValueError(f"Claude chose an unknown pattern: {pattern_m.group(1)}")
        pattern_cls = getattr(declare_patterns, cls_name)
        return {pattern_cls(*(Atomic(s) for s in symbols)): 1.0}


def to_ltlf2dfa_syntax(formula_str: str) -> str:
    """pylogics prints strong next as `X[!]` and weak next as `X`; ltlf2dfa
    (the runtime parser) spells them `X` and `WX`."""
    return re.sub(r"X(\[!\])?(?=\()", lambda m: "X" if m.group(1) else "WX", formula_str)


def paraphrase(formula_str: str) -> str:
    return asyncio.run(
        _ask_claude(
            "You paraphrase LTLf formulas over finite traces into one short plain-English "
            "sentence about the required order/occurrence of events. Reply with the "
            "sentence only.",
            formula_str,
        )
    )


def activities_of(rulebook_path: Path) -> list[str]:
    import yaml

    raw = yaml.safe_load(rulebook_path.read_text())
    return list((raw.get("activities") or {}).keys())


def append_rule(rulebook_path: Path, rule_id: str, description: str, formula: str, posture: str) -> str:
    original = rulebook_path.read_text()
    block = (
        f"\n  - id: {rule_id}\n"
        f"    description: {description}\n"
        f"    formula: \"{formula}\"\n"
        f"    posture: {posture}\n"
    )
    rulebook_path.write_text(original + block)
    return original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sentence", help="the policy sentence, in plain English")
    parser.add_argument("--rulebook", default=str(RULEBOOK_PATH))
    parser.add_argument("--id", required=True, help="rule id to append as")
    parser.add_argument("--posture", default="recorder", choices=["recorder", "tripwire", "seatbelt"])
    parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="propose only; do not touch the rulebook")
    args = parser.parse_args()

    _load_env()
    rulebook_path = Path(args.rulebook)
    activities = activities_of(rulebook_path)

    print(f"policy sentence:  {args.sentence!r}")
    print(f"vocabulary:       {', '.join(activities)}\n")

    engine = ClaudeEngine(activities)
    results = translate(args.sentence, engine=engine)
    if not results:
        print("✗ no pattern produced")
        return 1
    pattern = max(results, key=results.get)
    formula_str = to_ltlf2dfa_syntax(to_string(pattern.to_ltlf()))

    print(f"DECLARE pattern:  {pattern}")
    print(f"LTLf formula:     {formula_str}")
    print(f"pattern reading:  {pattern.to_english().strip()}\n")

    back = paraphrase(formula_str)
    print("── round trip ──────────────────────────────────────")
    print(f"you wrote:      {args.sentence}")
    print(f"the rule says:  {back}")
    print("────────────────────────────────────────────────────")

    if args.dry_run:
        print("\n(dry run — rulebook untouched)")
        return 0

    if not IN_CHECKOUT and rulebook_path.resolve() == EXAMPLE_RULEBOOK.resolve():
        print("✗ that is the bundled example rulebook — pass --rulebook <your.rules.yaml>")
        return 2

    if not args.yes:
        answer = input("Same thing? Append to rulebook and re-lint? [y/N] ").strip().lower()
        if answer != "y":
            print("aborted — the original sentence wins; a human should look closer.")
            return 1

    original = append_rule(rulebook_path, args.id, args.sentence, formula_str, args.posture)
    try:
        lint = subprocess.run(
            [sys.executable, "-m", "ltl_harness.lint_cli", str(rulebook_path.resolve())],
            capture_output=True,
            text=True,
        )
    except BaseException:
        rulebook_path.write_text(original)  # never leave an unverified rule behind
        raise
    print("\nguardrails on the guardrails (runtime lint):")
    print((lint.stdout + lint.stderr).rstrip())
    if lint.returncode != 0:
        rulebook_path.write_text(original)
        print(f"\n✗ lint failed — appended rule '{args.id}' was reverted.")
        return 1
    print(f"\n✓ rule '{args.id}' appended to {rulebook_path} (posture: {args.posture})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
