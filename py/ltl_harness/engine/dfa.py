"""Every LTLf formula is a little machine — for real, this time.

The JS prototype hand-compiled seven DECLARE templates. Here we compile any
LTLf formula to a minimal DFA with LTLf2DFA (De Giacomo & Favorito's tool,
backed by MONA), then color the states with the four RV-LTL verdicts:

    PERM_SAT   every state reachable from here is accepting
    CURR_SAT   accepting now, could still go wrong
    CURR_VIOL  not accepting now, but a future could repair it (open obligation)
    PERM_VIOL  no accepting state reachable — broken beyond repair

We parse MONA's raw DFA output rather than the DOT rendering: it is the
authoritative format (explicit accepting list, bit-pattern transitions with
X don't-cares). MONA state 0 is a dummy pre-initial state that reads one
padding symbol; the real initial state is its unique successor.

Trace semantics: exactly one activity per step (DECLARE's one-hot event
model). Reachability for coloring therefore only follows one-hot successors
plus the all-false "other" symbol — assignments with two activities true
cannot occur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ltlf2dfa.parser.ltlf import LTLfParser

COLORS = ("PERM_SAT", "CURR_SAT", "CURR_VIOL", "PERM_VIOL")
OTHER = "__other__"  # any mapped activity that is not a variable of this formula

_STATE_LABEL = {
    "PERM_SAT": "SATISFIED",
    "CURR_SAT": "OK SO FAR",
    "CURR_VIOL": "OBLIGATION OPEN",
    "PERM_VIOL": "VIOLATED",
}

_parser = LTLfParser()


@dataclass
class ColoredDFA:
    formula: str
    variables: list[str]                     # lowercase, MONA order
    initial: int
    accepting: set[int]
    states: list[int]
    transitions: dict[int, list[tuple[str, int]]]  # state -> [(bit-pattern, next)]
    colors: dict[int, str] = field(default_factory=dict)

    # ---------------- stepping ----------------

    def step_bits(self, state: int, bits: str) -> int:
        for pattern, nxt in self.transitions[state]:
            if all(p == "X" or p == b for p, b in zip(pattern, bits)):
                return nxt
        raise RuntimeError(
            f"MONA DFA incomplete: no transition from {state} on {bits} ({self.formula})"
        )

    def _bits_for(self, activity: str | None) -> str:
        return "".join("1" if v == activity else "0" for v in self.variables)

    def step(self, state: int, activity: str | None) -> int:
        """Advance on one trace event. Unknown/other activities are all-false."""
        return self.step_bits(state, self._bits_for(activity))

    def color(self, state: int) -> str:
        return self.colors[state]

    # ---------------- coloring ----------------

    def _one_hot_successors(self, state: int) -> set[int]:
        succ = {self.step(state, v) for v in self.variables}
        succ.add(self.step(state, None))  # the "other" event
        return succ

    def _compute_colors(self) -> None:
        reach: dict[int, set[int]] = {}
        for s in self.states:
            seen = {s}
            queue = [s]
            while queue:
                cur = queue.pop()
                for nxt in self._one_hot_successors(cur):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            reach[s] = seen
        for s in self.states:
            acc_reach = reach[s] & self.accepting
            if not acc_reach:
                self.colors[s] = "PERM_VIOL"
            elif acc_reach == reach[s]:
                self.colors[s] = "PERM_SAT"
            elif s in self.accepting:
                self.colors[s] = "CURR_SAT"
            else:
                self.colors[s] = "CURR_VIOL"

    # ---------------- UI serialization ----------------

    def to_machine_json(self, alphabet: list[str], template: str, english: str) -> dict:
        """Serialize in the exact shape the dashboard's FSM renderer expects."""
        state_ids = {s: f"s{s}" for s in self.states}
        edges: list[dict] = []
        other_edges: dict[str, str] = {}
        for s in self.states:
            other_target = self.step(s, None)
            by_target: dict[int, list[str]] = {}
            for act in alphabet:
                t = self.step(s, act)
                if t == other_target:
                    continue  # covered by the dashed "other" edge (or self-loop)
                by_target.setdefault(t, []).append(act)
            for t, acts in by_target.items():
                edges.append({"from": state_ids[s], "on": acts, "to": state_ids[t]})
            if other_target != s:
                other_edges[state_ids[s]] = state_ids[other_target]
        return {
            "template": template,
            "formula": self.formula,
            "english": english,
            "states": [
                {
                    "id": state_ids[s],
                    "label": _STATE_LABEL[self.colors[s]],
                    "color": self.colors[s],
                }
                for s in self.states
            ],
            "initial": state_ids[self.initial],
            "edges": edges,
            "otherEdges": other_edges,
        }


_VARS_RE = re.compile(r"DFA for formula with free variables:\s*(.*)")
_TRANS_RE = re.compile(r"State (\d+): ([01X]*) -> state (\d+)")
_ACCEPT_RE = re.compile(r"Accepting states:\s*([\d\s]*)")


@lru_cache(maxsize=256)
def compile_formula(formula: str) -> ColoredDFA:
    """LTLf formula -> colored automaton, via MONA."""
    parsed = _parser(formula)
    mona = parsed.to_dfa(mona_dfa_out=True)

    m = _VARS_RE.search(mona)
    if not m or not m.group(1).split():
        raise ValueError(f"Formula has no free variables: {formula!r}")
    upper_vars = m.group(1).split()

    # MONA uppercases symbols; map back to the formula's lowercase names.
    symbols = {s.lower() for s in re.findall(r"[a-z_][a-z0-9_]*", formula)}
    variables = []
    for uv in upper_vars:
        lv = uv.lower()
        if lv not in symbols:
            raise ValueError(f"MONA variable {uv} not found in formula {formula!r}")
        variables.append(lv)

    acc_m = _ACCEPT_RE.search(mona)
    accepting = {int(x) for x in acc_m.group(1).split()} if acc_m else set()

    transitions: dict[int, list[tuple[str, int]]] = {}
    for src, bits, dst in _TRANS_RE.findall(mona):
        transitions.setdefault(int(src), []).append((bits, int(dst)))

    if 0 not in transitions or len(transitions[0]) != 1:
        raise RuntimeError(f"Unexpected MONA pre-initial state for {formula!r}")
    initial = transitions[0][0][1]

    # keep states reachable from the real initial (drop the dummy state 0)
    states, queue = {initial}, [initial]
    while queue:
        cur = queue.pop()
        for _bits, nxt in transitions.get(cur, []):
            if nxt not in states:
                states.add(nxt)
                queue.append(nxt)

    dfa = ColoredDFA(
        formula=formula,
        variables=variables,
        initial=initial,
        accepting=accepting & states,
        states=sorted(states),
        transitions={s: transitions[s] for s in states},
    )
    dfa._compute_colors()
    return dfa
