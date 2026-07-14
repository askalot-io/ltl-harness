# LTL Guardrail Harness

**Temporal-logic guardrails for [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) agents.**
Rules like *"never issue a refund before the receipt has been verified"* live in a versioned
YAML rulebook — not in the system prompt. Each rule is compiled to a minimal deterministic
finite automaton by [LTLf2DFA](https://github.com/whitemech/LTLf2DFA)/[MONA](https://www.brics.dk/mona/),
advanced on every tool call via SDK hooks, and empowered to **deny** any call that would
permanently violate an enforced rule. No second LLM watching the first, no regex over
transcripts — a turnstile.

> **The agent proposes. The automaton disposes.**

This is the working implementation of the essay
[*Stop Begging Your Agent to Behave*](./stop-begging-your-agent-to-behave.md)
(hooks, traces, and a little temporal logic: real guardrails for agentic workflows).

![run view — the seatbelt denying a refund under prompt pressure](docs/dashboard-run.png)

## How it works

```
rules/*.yaml          policy: activities (world→symbol map) + DECLARE/LTLf rules
      │
      ▼  LTLf2DFA + MONA (design time, cached)
colored automata      minimal DFAs, states labeled with the four RV-LTL verdicts
      │
      ▼  Claude Agent SDK hooks (runtime, deterministic, no model in the loop)
PreToolUse            simulate the proposed call; deny if every possible outcome
                      permanently violates a seatbelt rule — and name the rule
PostToolUse           resolve the actual activity, advance every machine, append
                      the verdicts to the flight recorder (JSONL per run)
Stop                  block the run from ending while an "eventually" is unmet —
                      under finite-trace semantics the end of the run is judgment day
```

After every event each rule wears one of four colors:

| color | meaning |
|---|---|
| `PERM_SAT`  | fulfilled, whatever happens next |
| `CURR_SAT`  | fine so far, could still go wrong |
| `CURR_VIOL` | an obligation is open — fine mid-run, **a violation if the trace ends now** |
| `PERM_VIOL` | broken beyond repair (trap state) |

Each rule carries a **posture**, promoted like a release pipeline as confidence grows:
`recorder` (log only) → `tripwire` (alert on red) → `seatbelt` (deny / block).

## Quick start

Prerequisites: Python ≥ 3.10, [uv](https://docs.astral.sh/uv/) (or pip), and MONA
(`apt install mona` — Debian/Ubuntu; the compiler behind LTLf2DFA).

```bash
cp .env.example .env                       # add your Claude Code OAuth token
cd py
uv venv && uv pip install -e . pytest

.venv/bin/python -m pytest tests/                              # engine tests
.venv/bin/python -m ltl_harness.lint_cli ../rules/refund.rules.yaml   # verify the rulebook
.venv/bin/python -m ltl_harness.agent happy                    # run a live demo scenario
.venv/bin/python -m ltl_harness.server                         # dashboard on :8471
```

### Demo scenarios

The demo is the essay's refund agent: mock MCP tools, real Claude, and a rulebook it
cannot escape. Note what the system prompts are *not*: there is no IMPORTANT, no NEVER,
no MUST — the rules hold anyway.

| scenario | what happens | mechanism demonstrated |
|---|---|---|
| `happy`  | receipt verifies on the 2nd attempt; lookup → verify → refund → audit note | recorder: colored trace, all green |
| `rogue`  | verification keeps failing AND the prompt tempts the agent to refund anyway | **seatbelt**: `PreToolUse` denies `issue_refund`; the agent escalates instead |
| `sloppy` | prompt says to skip audit/bookkeeping steps | **Stop hook**: the run may not end while an obligation is open |

## The rulebook

Activities are the security boundary — the world→symbols mapping is deliberately dumb
(tool-name equality plus, optionally, one field of the tool result):

```yaml
activities:
  verify_receipt_ok:
    tool: mcp__refund__verify_receipt
    result: { path: verified, equals: true }
```

Rules are DECLARE templates (`Existence`, `Absence(n)`, `Response`, `Precedence`,
`ChainResponse`, `NotSuccession`, `Init` — list-valued params mean *any of*)…

```yaml
- id: no-refund-before-verification
  description: Never issue a refund before the receipt has been verified.
  template: Precedence
  params: { a: verify_receipt_ok, b: issue_refund }
  posture: seatbelt
```

…or, since every rule goes through a real LTLf compiler, any raw formula:

```yaml
- id: order-lookup-happens
  description: Every run must look up an order at least once.
  formula: "F(look_up_order)"
  posture: recorder
```

### Lint the rulebook before the flight

All machines run side by side as one product automaton, so whole-rulebook questions are
mechanical. `lint_cli` checks for **contradictions** (no compliant run exists at all),
**traps** (a reachable healthy state from which every continuation violates something —
reported with a counterexample trace), **dead rules**, and **shadowed rules**. The demo
agent refuses to start on a rulebook that fails verification; run it in CI on every edit.

## Natural language → rules (NL2LTL, design time)

The essay's role reversal: the LLM translates policy *at design time*, where a human
reviews the output once; deterministic code does the checking at runtime.

```bash
cd py && uv venv .venv-nl && uv pip install --python .venv-nl/bin/python nl2ltl claude-agent-sdk pyyaml

.venv-nl/bin/python -m ltl_harness.nl2rules \
  "Every refund must eventually be audited." --id audit-eventually --posture recorder
```

A Claude-backed [`nl2ltl`](https://github.com/IBM/nl2ltl) engine picks the DECLARE
pattern over the rulebook's vocabulary; the formula is paraphrased **back** to English
*without seeing your sentence* (the round-trip check — if the two sentences don't say
the same thing, the original wins and a human looks closer); on approval the rule is
appended and the runtime linter re-verifies the whole rulebook, reverting the append
if verification fails. Guardrails on the guardrails.

Why the second venv: `ltlf2dfa` needs legacy `lark-parser` 0.x while `nl2ltl`'s
`pylogics` needs `lark` ≥ 1.0 — both claim the same module path
([whitemech/LTLf2DFA#78](https://github.com/whitemech/LTLf2DFA/issues/78)). The two
sides only talk via files and subprocesses, mirroring the design-time/runtime split.

## The dashboard

![rulebook view — every rule is a little machine](docs/dashboard-rulebook.png)

- **Rule status over the trace** — rules × events matrix in the four colors; ⛔ columns
  mark calls the seatbelt denied (events that never happened). Click to time-travel.
- **State machines** — every rule's compiled DFA rendered live, current state
  highlighted, taken transition in blue.
- **Flight recorder** — every event, denial (with the rule that fired and why),
  stop-block, and the agent's final reply. One JSONL file per run: a complete account
  of what the agent actually *did*, independent of what it *said* it did.
- **Rulebook tab** — formulas, diagrams, and the live design-time verification verdict.

No build step, no frontend dependencies; the server is stdlib-only.

## Using it in your own agent

```python
from ltl_harness.engine.rulebook import load_rulebook
from ltl_harness.engine.lint import lint_rulebook
from ltl_harness.hooks import create_guardrails
from ltl_harness.recorder import Recorder
from claude_agent_sdk import ClaudeAgentOptions, query

rulebook = load_rulebook("rules/my.rules.yaml")
assert lint_rulebook(rulebook)["ok"], "rulebook is broken — refusing to fly"

recorder = Recorder("runs", "my-run", meta={...})
hooks, monitor, finalize = create_guardrails(rulebook, recorder)

async for message in query(prompt=..., options=ClaudeAgentOptions(..., hooks=hooks)):
    ...
verdicts, ok = finalize()   # judgment day: open obligations become violations
```

## Deployment

`deploy/` has the reference configs used in production:

- `ltl-harness.service` — systemd unit running the dashboard on `127.0.0.1:8471`
- `nginx-ltl-harness.conf` — nginx reverse proxy exposing it (port 8080)

```bash
cp deploy/ltl-harness.service /etc/systemd/system/   # adjust paths
systemctl enable --now ltl-harness
cp deploy/nginx-ltl-harness.conf /etc/nginx/sites-available/ltl-harness
ln -s /etc/nginx/sites-available/ltl-harness /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

## Repository layout

```
rules/            the rulebook (YAML) — policy as code, owned by humans
py/ltl_harness/   the harness
  engine/           formulas.py (DECLARE→LTLf) · dfa.py (MONA→colored DFA)
                    rulebook.py · monitor.py · lint.py
  hooks.py          PreToolUse / PostToolUse / Stop for the Agent SDK
  recorder.py       JSONL flight recorder
  agent.py          demo refund agent (mock MCP tools, three scenarios)
  server.py         dashboard server (stdlib)
  lint_cli.py       CI entry point — exit 1 on contradiction/trap
  nl2rules.py       NL → DECLARE → LTLf translator (runs in .venv-nl)
py/tests/         engine test suite
ui/               the dashboard (plain HTML/JS, no build)
runs/             flight records (gitignored)
deploy/           systemd + nginx reference configs
docs/             screenshots
```

## Honest limits

- **The activity mapping is the weak joint.** The automaton sees symbols; reality sends
  tool calls. Keep the mapping embarrassingly simple and audit it like the security
  boundary it is.
- **It governs the trace, not the text.** No temporal rule notices a rude email or a
  hallucinated clause. Content quality needs its own tools.
- **Rules only cover what someone thought to write.** The rulebook is a floor, not a
  ceiling — the flight recorder is where the missing rules announce themselves.

## References

- De Giacomo & Vardi, [*Linear Temporal Logic and Linear Dynamic Logic on Finite Traces*](https://www.ijcai.org/Proceedings/13/Papers/132.pdf) (IJCAI 2013) — LTLf semantics
- Pesic, Schonenberg & van der Aalst, [*DECLARE: Full Support for Loosely-Structured Processes*](https://doi.org/10.1109/EDOC.2007.14) (EDOC 2007) — the template catalog
- Maggi, Montali, Westergaard & van der Aalst, [*Monitoring Business Constraints with Linear Temporal Logic: An Approach Based on Colored Automata*](https://doi.org/10.1007/978-3-642-23059-2_13) (BPM 2011) — the four-color runtime monitoring
- Fuggitti & Chakraborti, [*NL2LTL — a Python Package for Converting Natural Language Instructions to LTL Formulas*](https://ojs.aaai.org/index.php/AAAI/article/view/27068) (AAAI 2023) — [github.com/IBM/nl2ltl](https://github.com/IBM/nl2ltl)
- [LTLf2DFA](https://github.com/whitemech/LTLf2DFA) / [MONA](https://www.brics.dk/mona/) — the formula→DFA toolchain
