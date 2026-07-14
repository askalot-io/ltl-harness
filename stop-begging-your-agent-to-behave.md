# Stop Begging Your Agent to Behave

*Hooks, traces, and a little temporal logic: real guardrails for agentic workflows*

**Peter Saghelyi** · 2026-07-04

Tags: ai-agents, llm, software-engineering, temporal-logic, guardrails

---

An AI agent looks trustworthy until it becomes real.

At the beginning it is only a description. *Handle customer refund requests. Look up the order, check the receipt, issue the refund, write a note for the audit log.*

Then reality enters. Never issue a refund before the receipt has been verified. If the receipt is missing, ask the customer for it — but at most three times, then hand the case to a human. Once a case has been escalated, don't touch the payment system again. Every refund must be followed by an audit note. Every request must end in *some* decision — a refund, a rejection, or an escalation — never in silence.

Suddenly the agent is no longer a description. It is a small process with rules. And where do those rules live today? In the system prompt:

> *"IMPORTANT: Always verify the receipt before issuing a refund."*
> *"NEVER call the payment API after a case has been escalated."*
> *"You MUST write an audit note after every refund. This is CRITICAL."*

Every capital letter is a confession. We write IMPORTANT and NEVER and MUST because we know, deep down, that we are not configuring the system. We are pleading with it.

## A prompt is a wish, not a rule

Large language models are probabilistic. That is not a flaw — it is the property that makes them useful. It is why they can read a messy customer email, why they can decide that "the thingy never arrived" is a delivery complaint. No rule engine does that.

But probabilistic also means: an instruction in the prompt is a strong suggestion, not a constraint. It competes for attention with everything else in the context window — the customer's angry message, the tool descriptions, the retrieved documents, the previous forty turns of conversation. On a long run, instructions drift. The agent that dutifully verified receipts in step 2 may, by step 23, be deep in a remediation loop where "just issue the refund and move on" looks locally reasonable.

The industry's current answers to this are revealing:

- Write the instruction again, louder, in more places.
- Add a second LLM to watch the first one.
- Read the transcripts afterwards and wince.

The first is superstition. The second is turtles all the way down — who watches the watcher? The third is not a guardrail at all; it is an autopsy.

Notice what all three have in common: they try to fix a reliability problem *inside* the probabilistic layer. That is the wrong level of abstraction. When something must *always* or *never* happen, you don't want a smarter wish. You want a mechanism that does not run on wishes.

## Hooks: the place where wishes become code

Most serious agent frameworks now expose **hooks**: points in the agent's loop where the framework calls *your* code — plain, deterministic, boring code — and lets it observe or intervene. Claude Code, for example, fires a `PreToolUse` hook before every tool call and a `PostToolUse` hook after it. The `PreToolUse` hook doesn't just watch: it can **allow, deny, or modify** the tool call before it happens.

That one architectural fact changes everything, because of what an agent run looks like from the hooks' point of view. Strip away the prose, the reasoning, the personality — what remains is a sequence of events:

```
step 1:  look_up_order        (order #4711 found)
step 2:  request_receipt      (email sent to customer)
step 3:  verify_receipt       (failed — image unreadable)
step 4:  request_receipt      (second attempt)
step 5:  verify_receipt       (success)
step 6:  issue_refund         ($84.90 refunded)
step 7:  write_audit_note     (note stored)
```

A run is a **finite trace**: it starts, some events happen in some order, and it ends. Every agent run — the good ones and the disasters — has this shape.

And a trace is something you can do two things with.

You can **record** it. A hook that appends one line per tool call gives you a flight recorder: a complete, structured account of what the agent actually did, independent of what it *said* it did. For any agent that touches money, records, or customers, this alone is worth the afternoon it takes to build.

And you can **constrain** it. The `PreToolUse` hook sees the tool call *before it executes*. If your code can decide, deterministically, "this event must not happen here," the framework will refuse the call — no matter how confident the model is.

Which raises the real question. Refuse *based on what?* A hook that blocks `rm -rf` is easy: the rule is about one event in isolation. But look back at our refund rules. Almost none of them are about one event in isolation.

## Rules about time, not about moments

An allowlist says: *this tool may be used, that tool may not.* A schema says: *this argument must be a positive number.* Both are rules about a single moment. Useful — and not remotely enough.

> *Never issue a refund before the receipt has been verified.*

This rule is not about `issue_refund` by itself. `issue_refund` is fine — it's the whole point of the agent. The rule is about the **order** of events: refund *after* verification is the happy path; refund *before* verification is the incident report.

> *Every refund request must eventually reach a decision.*

This rule is not about any event at all. It is about the **eventual shape of the whole run** — a promise that something will happen before the end.

> *Ask for the receipt at most three times.*

A rule about **how often** across the run.

> *After escalation, never touch the payment system again.*

A rule about **what the future may contain**, given the past.

Order, eventually, how often, never after — these are rules about *time*. To write them precisely, we need a small language extension: a handful of operators that talk about sequences instead of moments. This is where a little math enters. I promise it stays little.

Start with the ingredients we already have: simple true/false statements about a single step of the trace. Call them **propositions**:

```
issue_refund        — this step is a call to the refund tool
receipt_verified    — the verify tool has succeeded by this step
escalated           — the case has been handed to a human
```

(Where do these come from? From the hooks. The hook payload contains the tool name, its arguments, and — in `PostToolUse` — its result. Deriving "which propositions are true at this step" is a dictionary lookup, not an AI problem.)

Now the operators. There are only four you need, and each is a word you already use:

**G — "always."** `G p` means: *p holds at every step of the trace.* 

```
G (not delete_customer_record)
```

*At no point in this run is the delete tool called.* An allowlist, it turns out, is just the simplest temporal rule.

**F — "eventually."** `F p` means: *p holds at some step, now or later.*

```
F (refund_issued or rejected or escalated)
```

*Sooner or later, this case reaches a decision.* No run may simply trail off.

**X — "next."** `X p` means: *p holds at the very next step.*

```
G (issue_refund -> X write_audit_note)
```

*Whenever a refund happens, the immediately following step is the audit note.* Not "at some point" — *immediately*. Compliance people love this operator.

**U — "until."** `p U q` means: *p keeps holding until q happens — and q does happen.*

```
(not issue_refund) U receipt_verified
```

Read it out loud: *no refund until the receipt is verified.* That is our first rule, exactly as the policy manual states it — except now it is a formula, and a formula can be checked by a machine, mechanically, on every step of every run.

That's the whole toolbox. Four operators, combined with plain and/or/not. The result is called **Linear Temporal Logic** — "linear" because it describes events on a single timeline, which is precisely what an agent trace is.

## The small f

Temporal logic was invented for systems that never stop: elevators, network protocols, operating systems. Classical LTL therefore assumes the trace is *infinite* — the elevator keeps running forever, and "eventually" may lawfully mean "in a thousand years."

Agent runs are not like that. A run starts when the request arrives and ends when the case closes — or when the token budget dies. Every trace has a last step.

This matters more than it sounds. The variant we need is **LTLf** — Linear Temporal Logic over *finite* traces, put on solid foundations by De Giacomo and Vardi in 2013. Same four operators, one crucial change of meaning: the trace ends, and every promise comes due at the end.

Under finite semantics, `F decision_reached` doesn't mean "someday, perhaps." It means: **before this run terminates, a decision is reached — or the run is in violation.** The end of the trace is judgment day. An "eventually" that hasn't happened by then is not pending; it is *broken*.

For guardrails, this is exactly the semantics you want. A vague hope becomes a deadline.

## You don't have to write the formulas

At this point a fair objection: *my compliance team will never write `(not issue_refund) U receipt_verified`.*

They don't have to. It turns out that real-world process rules cluster into a couple dozen recurring shapes, and those shapes have names. The business-process community catalogued them years ago in a language called **DECLARE** — each pattern is a named template with a precise LTLf formula hiding inside:

| Template | Plain English | Refund agent example |
|---|---|---|
| `Existence(a)` | a happens at least once | Every run reaches a decision |
| `Absence(a, n)` | a happens fewer than n times | At most 3 receipt requests |
| `Response(a, b)` | after a, b must eventually follow | Refund → audit note follows |
| `Precedence(a, b)` | b can only happen after a | No refund before verification |
| `ChainResponse(a, b)` | after a, b must follow *immediately* | Refund → audit note is the very next act |
| `NotSuccession(a, b)` | once a happens, b never happens afterwards | After escalation, no payment calls |

Nobody on the team writes operators. The policy author writes — or already wrote, years ago, in the operations manual — sentences like *"escalated cases must not be processed further."* Mapping such sentences onto named templates is exactly the kind of constrained translation LLMs are good at, and there is prior art: IBM's open-source NL2LTL does precisely this, prompting an LLM to translate natural language into DECLARE patterns.

Note the role reversal, because it is delicious. The LLM is used *at design time* to translate policy into formal rules — a task where a human reviews the output once, before anything runs. At *runtime*, when there is real money in the pipeline and no human watching, the checking is done by deterministic code. The creative component and the trustworthy component swap places exactly where each belongs.

One safeguard makes the design-time translation honest: the **round trip**. Take each generated formula, have the LLM paraphrase it back into English, and show both sentences to the policy owner. *You wrote: "escalated cases must not be processed further." The rule says: "once escalate_to_human occurs, issue_refund never occurs afterwards." Same thing?* If yes, ship it. If no, the original sentence wins and a human looks closer. Formal-methods researchers use this same pattern to catch mistranslations — it catches the LLM's occasional wrong turn without requiring anyone to read logic.

## The trick that makes it enforceable: every rule is a little machine

Here is the fact that turns all this theory into a practical guardrail, and it deserves its own drum roll:

**Every LTLf formula can be compiled into a finite state machine.**

A state machine is the least intimidating object in computer science. It is a handful of states, one of which is "current," and a rule for moving between them when an event arrives. A subway turnstile is a state machine: *locked* becomes *unlocked* when a coin arrives, *unlocked* becomes *locked* when a person pushes through. No memory beyond the current state. No cleverness. That is the point.

Compile `(not issue_refund) U receipt_verified` and you get, essentially, this:

```
                  verify succeeds
   [ WAITING ] ────────────────────► [ SATISFIED ]
        │                                (stays here,
        │  issue_refund                   rule fulfilled)
        ▼
   [ VIOLATED ]
    (trap state —
     no way back)
```

Three states. The machine starts in WAITING. Every event in the trace nudges it. If `verify_receipt` succeeds, the machine settles into SATISFIED and this rule never complains again. If `issue_refund` arrives while still WAITING — the machine falls into VIOLATED, a trap with no exit, because no future event can un-refund the money before the verification. *The order was wrong, and order cannot be repaired retroactively.*

Now put one such machine — researchers who monitor business processes this way call them *colored automata* — next to every rule in your rulebook, and wire them into the hooks:

**On `PostToolUse`:** translate the event into propositions, advance every machine one step, and append the verdicts to the flight-recorder log. Each rule, at each step, wears one of four colors:

- **permanently satisfied** — fulfilled, whatever happens next (the verification came through);
- **currently satisfied** — fine so far, could still go wrong;
- **currently violated** — an obligation is open (an "eventually" still unmet — fine mid-run, fatal at the end);
- **permanently violated** — broken beyond repair, no possible future fixes it.

**On `PreToolUse`:** before the tool actually runs, simulate. Feed the *proposed* event to copies of the machines and ask: does any rule land in **permanently violated**? If yes — deny, and name the rule:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Blocked by rule 'no refund before receipt verification' (Precedence): verify_receipt has not succeeded in this run."
  }
}
```

Stop and appreciate what this is *not*. It is not a second LLM judging the first — there is no model in this loop at all. It is not a regex over the transcript. It is a turnstile: a deterministic machine that advances one state per event, in microseconds, and its refusals are not judgment calls but *theorems* — "no continuation of this trace satisfies the rule." The model remains free everywhere the rules are silent: free to reason, to phrase, to choose tools, to be creative about *how*. The trace, however, will conform to the rulebook about *what and when* — not because we asked nicely in the prompt, but because the mechanism sits outside the thing it constrains.

The agent proposes. The automaton disposes.

## Three postures: recorder, tripwire, seatbelt

You do not have to jump to blocking on day one. The same machinery supports three postures, in escalating order of confidence:

**Recorder.** Hooks only log: each event, each rule's color after it. You learn how your agent *actually* behaves across hundreds of runs — including the near-misses that current-transcript-reading would never surface. ("Rule 'audit note follows refund' spends 40% of runs in *currently violated* for more than five steps. Why?")

**Tripwire.** Violations don't block; they alert. A rule going red mid-run pings a human, opens a ticket, or pauses the agent for review. Right for rules where the mapping from events to propositions is still being tuned.

**Seatbelt.** `PreToolUse` denies any call that would make a rule permanently violated. Right for the rules where you'd rather have a false refusal than a true incident: payments, deletions, external communications, anything legal reads about later.

Run new rules through the postures like a promotion pipeline: record for two weeks, tripwire until the false positives are boring, then enforce. Every promotion is backed by data from the previous stage — which the recorder has been collecting all along.

## Lint the rulebook before the flight

One more gift falls out of the compilation trick, and it may be the most underrated one.

Rulebooks are written by many hands over a long time. The platform team contributes baseline rules; the industry pack adds regulatory ones; the customer adds their own overrides. Nobody reads the union. And a rulebook can be broken *as a whole* in ways no individual author can see:

- **Contradiction.** One rule demands `F audit_note`; another, added two quarters later, forbids write access in this context. Together they are unsatisfiable — *no possible run* complies. Your agent is doomed before the first token, and the error message will look like the model misbehaving.
- **Dead rule.** A rule guards a tool that no longer exists, or fires on a state that upstream rules make unreachable. It costs attention forever and protects nothing.
- **Shadowed rule.** A customer's override quietly subsumes a platform compliance rule — the platform rule can never trigger again, and nobody decided that on purpose.
- **Trap.** A reachable situation where every continuation violates *something* — the agent's only options are different flavors of red. The run stalls, is force-killed, and the incident review blames the model for "getting stuck."

Because every rule is an automaton, the *whole rulebook* is one big automaton (run all the machines side by side), and these questions become mechanical: *Does any accepting trace exist at all? Is there a trace that exercises rule 17? Can a trace reach a state with no legal exits?* When the answer is bad, the checker doesn't just say no — it hands you a concrete counterexample trace, a short story of a run that walks into the trap, which is worth a hundred abstract warnings.

This is design-time verification, and it runs before any agent does — in CI, on every edit to the rulebook. In our implementation the rulebook lives in versioned files, and a `PostToolUse` hook watches for edits to those files and re-runs the verification automatically. When an agent (or a human) edits the guardrails, the guardrails on the guardrails fire. It is hooks all the way down, and for once that is a compliment.

## What this does not solve

Honesty section. The mechanism is strong precisely because its scope is narrow, so let's draw the border.

**The mapping is the weak joint.** The automaton sees propositions; reality sends tool calls. If `receipt_verified` is derived incorrectly — the verify tool "succeeded" on the wrong document — the monitor cheerfully approves a bad refund. Formal guarantees hold *modulo the translation from world to symbols*. Keep that translation embarrassingly simple (tool names, exit codes, explicit result fields), and audit it like the security boundary it is.

**It governs the trace, not the text.** No temporal rule notices that the refusal email the agent wrote was rude, that the summary hallucinated a policy clause, or that the model was manipulated into *wanting* something silly. Content quality still needs its own tools: evals, sampling, human review. Temporal guardrails govern *what happened and when* — that is a lot, and it is not everything.

**Rules only cover what someone thought to write.** The rulebook is a floor, not a ceiling. The verifier can prove the rules are consistent; it cannot know the rule you forgot. (Though the flight recorder helps here too: surprising traces are where missing rules announce themselves.)

**Someone must own the rulebook.** Rules encode policy, and policy has owners, versions, and disputes. The formal layer makes disagreements *visible* — two contradictory rules now fail loudly in CI instead of silently in production — but a human still has to decide which one wins.

## Why this matters more, not less, as agents get better

It is tempting to file all this under "training wheels for weak models" — surely GPT-next or Claude-next will just follow instructions?

The trend runs the other way. Agents are getting *more* autonomous: longer runs, more tools, more consequential actions, less human attention per decision. Agents now write other agents' configurations; rulebooks are assembled from layers owned by different organizations; a single platform hosts thousands of agent variants doing structurally similar work under structurally different policies. Reading transcripts does not scale to that world. Prompt discipline does not compose across organizational boundaries.

A trace-based guardrail does. It doesn't care which model is running, how it was prompted, or who wrote its instructions. It watches events at the only boundary that ultimately matters — where the agent touches the world — and it speaks a language precise enough to verify and plain enough to read back to a policy owner as an English sentence.

The division of labor is the same one I argued for in questionnaire design, and I suspect it is a general law of building with LLMs: **the model supplies judgment; the structure supplies guarantees; a human remains responsible for meaning.** The LLM decides *how* to pursue the case. The temporal rules decide *what may follow what*. The policy owner decides what the rules should say.

## Conclusion

We have been treating agent safety as a prompting problem — pleading in capital letters and hoping the plea survives forty turns of context. But an agent run, seen from its hooks, is something much more tractable than a conversation: it is a finite trace of events. And finite traces of events are a solved genre. We know how to state rules over them (four operators you already use as English words), how to make the rules readable (named templates instead of formulas), how to enforce them (each rule compiled to a small deterministic machine, advanced on every hook event, empowered to refuse), and how to lint the whole rulebook before anything runs.

None of it constrains the model's intelligence. All of it constrains the model's *footprint*.

That is the shift agentic engineering needs:

**From prompts to promises. From transcripts to traces. From hoping to checking.**

## Notes and further reading

The ideas here stand on two decades of work in temporal logic, declarative process modeling, and runtime verification. Starting points:

- **De Giacomo & Vardi**, *Linear Temporal Logic and Linear Dynamic Logic on Finite Traces* (IJCAI 2013) — the paper that put LTLf on solid ground: same operators as classical LTL, finite-trace semantics. The "small f" section above is this paper in street clothes.
- **Pesic, Schonenberg & van der Aalst**, *DECLARE: Full Support for Loosely-Structured Processes* (EDOC 2007) — the template catalog (Response, Precedence, Absence, …) that lets domain experts state temporal rules without writing formulas.
- **Maggi, Montali, Westergaard & van der Aalst**, *Monitoring Business Constraints with Linear Temporal Logic: An Approach Based on Colored Automata* (BPM 2011) — the runtime-monitoring construction: compile each constraint to an automaton, advance it per event, color each rule's status four ways. The guardrail engine above is this idea relocated into agent hooks.
- **Fuggitti & Chakraborti**, *NL2LTL* (AAAI 2023; open source from IBM Research) — LLM-driven translation of natural-language requirements into DECLARE/LTL patterns. Evidence that the policy-to-rules step can be automated with review, not authored by logicians.
- **Mendoza et al.**, *Translating Natural Language to Temporal Logics with Large Language Models and Model Checkers* (FMCAD 2024) — the round-trip validation pattern for keeping LLM translations honest.
- **LTLf2DFA / MONA** — the open-source toolchain that compiles LTLf formulas to minimal deterministic automata; the practical engine behind "every rule is a little machine."
- **Claude Code hooks documentation** — the concrete `PreToolUse` / `PostToolUse` mechanism used as the running example; other agent frameworks expose equivalent interception points.

*If you publish this elsewhere as well, set a canonical link to the original so search engines know the source.*
