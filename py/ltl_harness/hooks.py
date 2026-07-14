"""Guardrail hooks for the Python Claude Agent SDK.

Same three interventions as the JS harness:

    PreToolUse   simulate the proposed event; deny if every possible outcome
                 permanently violates a seatbelt rule
    PostToolUse  resolve the actual activity, advance every machine, record
    Stop         block the run from ending while a seatbelt rule still has an
                 open obligation (finite-trace semantics: an unmet
                 "eventually" is a violation at the end of the trace)
"""

from __future__ import annotations

from claude_agent_sdk import HookMatcher

from .engine.monitor import RunMonitor
from .engine.rulebook import Rulebook, candidate_activities, extract_result, resolve_activity
from .recorder import Recorder

MAX_STOP_BLOCKS = 3


def create_guardrails(rulebook: Rulebook, recorder: Recorder | None):
    monitor = RunMonitor(rulebook)
    stop_blocks = 0

    async def pre_tool_use(input_data, tool_use_id, context):
        candidates = candidate_activities(rulebook, input_data["tool_name"])
        decision = monitor.decide(candidates)
        if decision["decision"] != "deny":
            return {}
        reasons = "; and rule ".join(
            f"'{rid}' ({rulebook.rule(rid).template}): {rulebook.rule(rid).description}"
            for rid in decision["denied_by"]
        )
        reason = (
            f"Blocked by temporal guardrail — this call would permanently violate rule "
            f"{reasons}. No continuation of the current trace could repair it."
        )
        if recorder:
            recorder.write(
                {
                    "type": "denial",
                    "step": monitor.step,
                    "tool": input_data["tool_name"],
                    "input": input_data.get("tool_input"),
                    "candidates": decision["per_candidate"],
                    "deniedBy": decision["denied_by"],
                    "reason": reason,
                }
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def post_tool_use(input_data, tool_use_id, context):
        activity = resolve_activity(
            rulebook, input_data["tool_name"], input_data.get("tool_response")
        )
        if activity is None:
            return {}  # not part of the mapped alphabet — invisible to the trace
        result = monitor.advance(activity)
        alerts = []
        for rid in result["newly_violated"]:
            rule = rulebook.rule(rid)
            if rule.posture in ("tripwire", "seatbelt"):
                alerts.append(
                    {
                        "rule": rid,
                        "posture": rule.posture,
                        "permanent": rid in result["newly_perm_violated"],
                        "message": f"Rule '{rid}' went red after '{activity}': {rule.description}",
                    }
                )
        if recorder:
            recorder.write(
                {
                    "type": "event",
                    "step": monitor.step,
                    "tool": input_data["tool_name"],
                    "activity": activity,
                    "input": input_data.get("tool_input"),
                    "result": extract_result(input_data.get("tool_response")),
                    "states": result["states"],
                    "alerts": alerts,
                }
            )
        return {}

    async def stop(input_data, tool_use_id, context):
        nonlocal stop_blocks
        open_obligations = monitor.open_obligations()
        enforced = [o for o in open_obligations if o["posture"] == "seatbelt"]
        blocked = bool(enforced) and stop_blocks < MAX_STOP_BLOCKS
        if recorder:
            recorder.write(
                {
                    "type": "stop_check",
                    "step": monitor.step,
                    "openObligations": open_obligations,
                    "blocked": blocked,
                }
            )
        if blocked:
            stop_blocks += 1
            owed = "; ".join(f"'{o['id']}': {o['description']}" for o in enforced)
            return {
                "decision": "block",
                "reason": (
                    "The run cannot end yet — these temporal rules still have open "
                    "obligations (an \"eventually\" that has not happened is a violation "
                    f"at the end of the trace): {owed} Complete them, then finish."
                ),
            }
        return {}

    hooks = {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "Stop": [HookMatcher(hooks=[stop])],
    }

    def finalize() -> tuple[dict, bool]:
        verdicts = monitor.finalize()
        ok = all(v["verdict"] == "SATISFIED" for v in verdicts.values())
        if recorder:
            recorder.write({"type": "run_end", "step": monitor.step, "verdicts": verdicts, "ok": ok})
        return verdicts, ok

    return hooks, monitor, finalize
