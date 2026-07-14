"""RunMonitor: one colored DFA per rule, advanced in lockstep with the trace.

Direct port of the JS monitor; the machines underneath are now MONA-minimal
DFAs instead of hand-built templates, but the interface — advance, simulate,
decide, finalize — is unchanged.
"""

from __future__ import annotations

from .rulebook import Rulebook


class RunMonitor:
    def __init__(self, rulebook: Rulebook):
        self.rulebook = rulebook
        self.step = 0
        self.states: dict[str, int] = {r.id: r.dfa.initial for r in rulebook.rules}

    def snapshot(self) -> dict:
        return {
            r.id: {"state": f"s{self.states[r.id]}", "color": r.dfa.color(self.states[r.id])}
            for r in self.rulebook.rules
        }

    def advance(self, activity: str) -> dict:
        self.step += 1
        changed, newly_violated, newly_perm_violated = [], [], []
        for r in self.rulebook.rules:
            before = self.states[r.id]
            before_color = r.dfa.color(before)
            after = r.dfa.step(before, activity)
            self.states[r.id] = after
            after_color = r.dfa.color(after)
            if before != after:
                changed.append(r.id)
            bad_now = after_color in ("CURR_VIOL", "PERM_VIOL")
            bad_before = before_color in ("CURR_VIOL", "PERM_VIOL")
            if bad_now and not bad_before:
                newly_violated.append(r.id)
            if after_color == "PERM_VIOL" and before_color != "PERM_VIOL":
                newly_perm_violated.append(r.id)
        return {
            "states": self.snapshot(),
            "changed": changed,
            "newly_violated": newly_violated,
            "newly_perm_violated": newly_perm_violated,
        }

    def simulate_one(self, activity: str) -> list[str]:
        """Rules that would land in PERM_VIOL if this event happened now."""
        violations = []
        for r in self.rulebook.rules:
            before = self.states[r.id]
            if r.dfa.color(before) == "PERM_VIOL":
                continue  # already dead
            if r.dfa.color(r.dfa.step(before, activity)) == "PERM_VIOL":
                violations.append(r.id)
        return violations

    def decide(self, candidates: list[str]) -> dict:
        """PreToolUse decision: deny only when EVERY possible outcome of the
        proposed call permanently violates some seatbelt rule."""
        if not candidates:
            return {"decision": "allow", "per_candidate": [], "denied_by": []}
        seatbelts = {r.id for r in self.rulebook.rules if r.posture == "seatbelt"}
        per_candidate = []
        for activity in candidates:
            violations = self.simulate_one(activity)
            per_candidate.append(
                {
                    "activity": activity,
                    "violations": violations,
                    "blocking": [v for v in violations if v in seatbelts],
                }
            )
        doomed = all(c["blocking"] for c in per_candidate)
        denied_by = sorted({v for c in per_candidate for v in c["blocking"]}) if doomed else []
        return {
            "decision": "deny" if doomed else "allow",
            "per_candidate": per_candidate,
            "denied_by": denied_by,
        }

    def open_obligations(self) -> list[dict]:
        snap = self.snapshot()
        return [
            {"id": r.id, "posture": r.posture, "description": r.description}
            for r in self.rulebook.rules
            if snap[r.id]["color"] == "CURR_VIOL"
        ]

    def finalize(self) -> dict:
        """Judgment day: every promise comes due at the end of the trace."""
        snap = self.snapshot()
        return {
            rid: {
                **s,
                "verdict": "VIOLATED" if s["color"] in ("CURR_VIOL", "PERM_VIOL") else "SATISFIED",
            }
            for rid, s in snap.items()
        }
