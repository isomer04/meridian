# Architecture

## The thesis

> A deterministic orchestrator owns control flow. LLM agents supply judgment inside it, and are
> never trusted with control flow, arithmetic, or irreversible side effects. And the whole thing
> is measured, because a multi-agent system you haven't evaluated is a demo, not a system.

Loan origination is a good domain for that claim because it has all three failure modes in one
workflow: decisions that must be **attributable**, arithmetic that must be **exact**, and side
effects that are **partly irreversible**. A hallucinated guideline citation is a compliance
event. A DTI computed one point wrong is a repurchase. A duplicated credit pull is a permanent
second hard inquiry on a borrower's report.

Three separations follow, and every design decision below is downstream of them:

| Concern | Owner | Never |
|---|---|---|
| Control flow | `flow.py` — `@start` / `@listen` / `@router` over typed state | never a model |
| Arithmetic | `calc/` — pure functions, tested against worked examples | never a model |
| Irreversible side effects | `tools.py` gateway + `core/saga.py` + `core/policy.py` | never unguarded |
| Judgment | three production agents | deterministic steps and human gates own everything else |

---

## Decomposition: what earns an agent

**The rule: its own judgment, its own tool set, and its own failure mode.** Anything fully
determined by its inputs is a *function*.

That rule is the honest answer to *"why is this an agent and not a function?"* Applying it after
the architecture review produced three production agents. The former eight-agent shape remains
available under `--roster demo` because its conditional menus and handoff are useful demonstrations.

### Production roles

| Agent | Own judgment | Own tools | Own failure mode |
|---|---|---|---|
| `collateral_agent` | exercise the value acceptance offer? | search, LTV | accepts weak collateral evidence or needlessly triggers the G3 cost gate |
| `underwriter_agent` | does the overlay bind; is the exception available? | none — decides on facts given | approves what the lender won't buy, or denies what it would |
| `compliance_qc_agent` | does the section entail the claim; is this rationale specific enough? | citation verification | passes a logical inversion or prohibited-basis rationale |

Intake validation, credit context, typed income dispatch, and retrieval are deterministic
production steps. The detailed production/demo split is in [agents.md](agents.md).

### Designed and deliberately not built

A roadmap that explains *why* is judgment; a roadmap that just lists things is a wish.

| Not built | Why |
|---|---|
| `aus_submission_agent` | Submission is a **deterministic transformation** of assembled state into a casefile. There is no judgment in it — no tool choice, no ambiguity. It is `vendors/aus.py`, called from the Flow as a saga step. Making it an agent would have been decoration, and it would have put a model between the computed figures and the submission, which is exactly what this design avoids. |
| `title_agent` | Real, and genuinely a role with its own judgment (clearing clouds on title, reading exceptions). Cut for **scope**, not principle: it needs a title-search vendor, a commitment document model, and an exceptions taxonomy, and none of that is exercised by the four demo scenarios. This is the most defensible next agent to build. |
| standalone `document_classifier` | Classifying a document is an **extraction step inside intake**, not a role. A pipeline stage does not earn an agent. It lives inside `intake_agent`, whose output includes the document types. |
| standalone `conditions_agent` | Conditions **fall out of** the underwriting decision — the same reasoning that grants an overlay exception writes the condition recording it. Split out, its only input would be another agent's output, and it would have been a formatting step wearing a role. |

Those exclusions still apply. The production reduction goes further: roles whose behavior is
fully covered by typed dispatch and tested functions no longer consume model calls.

---

## The judgment seam

The single most useful structural decision in the repo, and the one I would lead with.

`judgment.py` defines a seven-method protocol. Two implementations satisfy it:

- **`StubJudgment`** — rules. Evaluates the overlay policy for real against the same corpus the
  agents cite. Not a lookup table of expected answers; if it were, the eval baseline it provides
  would be worthless.
- **`CrewJudgment(roster="production")`** — three agents; deterministic intake, income, credit,
  and retrieval.
- **`CrewJudgment(roster="demo")`** — the retained eight-agent demonstration.

Both drive the **identical** Flow, routers, saga, tool gateway and policy controls. Consequences:

1. **Failures localise.** If a case is wrong under `crew` but right under `stub`, the judgment
   changed. If it's wrong under both, the orchestration is wrong. Most agent systems cannot tell
   you which, and that is usually where debugging time goes.
2. **The system runs with no API key.** A reviewer cloning this gets a working demo — real Flow,
   real saga, real controls, real retrieval, real citation verification — without secrets.
3. **Build order was risk order.** `core/`, `calc/`, `vendors/`, `rag/` have zero CrewAI imports.
   Had the framework fought the install, there was still a shippable system.

The cost is a maintained interface and two implementations of the same policy, which is real
duplication. It was worth it here because the rules are small and the diagnostic value is high.
On a system whose judgment could not be expressed as rules, this trade would not pay.

---

## Deterministic vs. agentic tool calling

Both, side by side, because the interesting thing is the boundary:

- **Deterministic:** the Flow's `@router` methods pick the path from typed state. No model.
- **Deterministic:** the *menu* an agent is offered — `tools_for_income_analyst(income_type)`
  omits `calc_self_employed_income` on a W-2 file. The agent is not trusted to avoid it; it
  cannot reach it.
- **Agentic:** the *choice within the menu*. Agents #2, #5 and #6 pick their own tools.
- **Agentic:** the *handoff*. `income_analyst_agent` is the only agent with
  `allow_delegation=True`, and it decides to escalate.

Three different mechanisms inside one step, and the eval harness measures the handoff as a
behaviour in **both directions** — a self-employed file must delegate, and a W-2 file must not.
An agent that always delegates would score 100% on the first check alone, which is why the second
exists.

Every `@router` in `flow.py` branches on a typed field — a `Decision` enum, a bool, a float from
`calc/`. None parses prose. The moment a router reads a model's sentence to find the path, the
model owns control flow and the thesis is gone.

---

## Reliability layer

Kept at demo depth deliberately. Three pieces stay because they are cheap, genuinely
differentiating, and honest answers to *"how does this survive production?"*

### Saga compensation

| Step | Execute | Compensate |
|---|---|---|
| `pull_credit` | hard inquiry (tri-merge) | **none possible** — permanent |
| `submit_to_aus` | DU findings | none needed — read-only |
| `order_appraisal` | $600 to the AMC | cancel — refundable **pre-inspection only** |
| `lock_rate` | pricing exposure | release — relock at **worst-case pricing** |

Two rows do not restore prior state, and keeping them is the point. **A saga is not a rollback.
It is a sequence of business-level apologies, and some apologies cost money.** In scenario 3 the
appraiser has already been out, so the ledger records $600 *not recovered* — the honest outcome,
not the clean one.

`compensate_all` unwinds in reverse and records `none_possible` distinctly from `skipped`,
because "we could not" and "we did not need to" are different facts about the world.

### Idempotency

```
key = sha256(loan_id | saga_step_name | attempt_epoch)
```

**The key must not depend on model output.** Hashing the tool arguments — the obvious design —
breaks precisely where it matters: an agent retrying with `"123-45-6789"` and then `"123456789"`
produces two keys and two hard inquiries. `attempt_epoch` increments only when the orchestrator
deliberately intends a fresh call, e.g. credit ageing out at 120 days.

Tested both ways: argument drift must not re-fire, and a deliberate epoch bump must.

### One source of truth

CrewAI's `@persist()` writes flow state to **its own** SQLite database with its own commit
boundary. Two state stores with two commit boundaries cannot support an atomicity claim, and
"where does the state live?" is the first thing a systems reviewer asks — two answers is a bad
answer.

So flow state is a **cache**, and `meridian.db` is the authority. Authoritative state is written
to `loan_state` inside `transaction()` at every step boundary, and each saga step commits its
result, its ledger row and its idempotency key **together**. `Saga.restore()` rebuilds completed
steps from `saga_log` alone.

Demonstrated rather than asserted: `--kill-after submit_to_aus`, then re-run. The bureau is not
called again.

### Two error classes

| Class | Behaviour | Why |
|---|---|---|
| `VendorError` | caught at the gateway, returned to the agent as a string | a bureau timeout is worth reasoning about — retry, try another source, raise a condition |
| `PolicyViolation` | **propagates, aborts the task, never stringified** | this is a control, not a guardrail |

If a compliance refusal came back as text, the agent would rephrase and try again, and *"an agent
that cannot do it"* would degrade into *"an agent that was told no once."*

### Concurrency, stated plainly

Single process. One shared WAL connection. Crews run their tasks sequentially. **Strictly this is
a durable workflow, not a distributed saga** — same pattern, one process. What changes across a
service boundary: compensation needs at-least-once delivery and therefore idempotent
compensators, the ledger needs to survive a partitioned writer, and "unwind in reverse" stops
being a `for` loop over local state.

---

## Deterministic retrieval and semantic citation QC

Three corpora, 52 chunks: agency selling guide, FHA 4000.1, and internal overlays that are
**stricter than agency**. Post-AUS the overlay layer is the whole job: **DU says
Approve/Eligible; the overlay may still say no.** Reconciling that is `underwriter_agent`'s real
task, and a far better retrieval showcase than answering guideline questions in the abstract.

`numpy` cosine over ~180 readable lines, **deliberately not chromadb**: the reference project on
disk carries the comment *"pin to avoid embedchain/langchain-openai conflicts"* and had to drop
CrewAI to `0.186.1` to resolve it. A dependency that constrains the framework version is worse
than some readable linear algebra.

Production retrieval is deterministic. The eight-agent demo roster retains the historical
agent-driven retrieval path solely as a demonstration. The production boundary is explicit:

- **Routing is deterministic.** Product type selects the corpus set, and overlays are **always**
  queried because overlays win (OV-GEN-01). Letting a model decide whether to consult the binding
  policy layer would be a design error dressed as autonomy.
- **Reformulation is deterministic.** A closed regex/template table covers the typed question set.
- **Self-critique is deterministic.** `critique_retrieval` asks whether the chunks answer the question;
  requery if not, capped at 3 rounds. An uncapped critique loop is not thoroughness.
- **Citations are enforced in a fixed order.** Deterministic existence and lexical-support checks
  run first; `compliance_qc_agent` then judges entailment and may only downgrade; `evals/` reports
  the resulting rate and its ceiling.

### The citation checker's ceiling

Support is scored by term overlap plus numeric-threshold agreement, with a synonym table
(`LTV`/`loan-to-value`, `primary`/`principal residence`) so vocabulary drift doesn't cause false
positives, and with section identifiers stripped before numbers are extracted so `OV-VA-01`
doesn't read as asserting "01".

That deterministic floor catches a fabricated section, a wrong threshold, and an off-topic claim.
A surviving claim then reaches a typed entailment task inside `compliance_qc_agent`; the judge can
only downgrade and the inverted "greater of" LTV claim is a regression case. The new ceiling is
the judge itself: it can falsely reject support or miss a subtle contradiction, and none of these
checks establishes that the hand-written corpus matches a real Selling Guide.

---

## Domain corrections worth knowing about

Recorded because they were mistakes an earlier design made, and because a home-lending reviewer
would catch each one immediately.

**There must be an AUS step.** Real conventional origination runs through Desktop Underwriter or
Loan Product Advisor: submit, receive findings, work the conditions. A system with no AUS reads as
designed by someone who has never seen a live file. It also improves the architecture — it makes
the underwriter's job *reconciliation* rather than inventing a decision from raw guidelines.

**"Appraisal waiver" is the wrong term and the wrong mechanism.** Fannie removed it from the
Selling Guide and replaced it with **value acceptance** (Freddie: ACE). It is **Fannie's offer,
issued through DU** — a lender does not grant one from its own LTV logic. An earlier design had
`LTV ≤ 80 → try waiver`, which is backwards. The offer arrives in findings; the agent decides
whether to exercise it. That inversion is what turns `collateral_agent` from a rule into a role.

**Recommendation and eligibility are independent.** Approve/Ineligible is a real combination and
is not collapsed.

**Smaller details that signal familiarity:** credit is a tri-merge and the representative score is
the middle of three; a duplicate inquiry is deduped for *scoring* in a 14–45 day window but
**remains on the report**; conditions are PTD / PTF / **PTC** / at-close — four, and PTC is the
one people forget; adverse action is **30 days** under ECOA/Reg B; purchase LTV uses the **lesser
of** price or appraised value.

---

## Where this actually breaks

Two honest answers, because it is the best question you can be asked.

**1. The overlay rules are encoded twice.** `StubJudgment` holds them as Python constants and the
corpus holds them as prose the agents retrieve. Change `OV-DTI-02` from 43% to 44% in
`internal_overlays.md` and the stub silently keeps using 43. Nothing detects the divergence. The
right fix is to make the corpus the single source and have the stub parse thresholds out of it,
so both paths read the same authority. Today the eval harness would only catch this if a golden
case happened to straddle the changed threshold.

**2. Retrieval sufficiency is judged lexically, and the offline embedder is lexical too.** With no
API key, retrieval is character-n-gram hashing and `critique_retrieval` scores term overlap. Both
degrade on paraphrase in the same direction, so a query phrased unlike the corpus can fail twice
and the critique loop will burn all three rounds without improving. Retrieval numbers from the
offline path should not be compared to the OpenAI-embedder path, which is why the report records
which embedder ran.

A third, lesser: cost and latency come from the provider's usage figures and are reported as `0`
when unavailable rather than estimated. Honest, but it means a provider that omits usage produces
a silently free-looking run.

---

## Roadmap, with reasons

**Cut as over-engineering for this scope** — all correct patterns, none of them what a multi-agent
system is judged on:

- **Transactional outbox + relay.** Correct for publishing events across a boundary. There is no
  boundary here.
- **PENDING leases on idempotency keys.** Needed when a process can die mid-call and another can
  pick it up. Single process, so a crashed call is retried by the same operator, and for a credit
  pull the safe policy is escalate-to-human rather than auto-retry anyway.
- **`COMPENSATION_FAILED` state and a startup recovery sweep.** `CompensationError` surfaces to
  the operator instead. In production you would park the loan and alert.
- **Hash-chained audit ledger.** Tamper-evidence matters to an examiner; it does not demonstrate
  anything about agent design.

**Genuinely next, in order:**

1. **Make the corpus the single source of overlay thresholds** — closes the divergence above.
2. **`title_agent`** — the most defensible next agent.
3. **Adversarial / prompt-injection eval suite.** One probe is exercised in the demo; one probe is
   not a suite.
4. **Postgres + optimistic concurrency.** SQLite WAL does not survive multiple writers, and the
   moment there are two workers the current design is wrong rather than merely limited.
5. **Real PDF/OCR ingestion.** Currently pre-extracted JSON plus one classification step.
6. **Model routing and per-loan cost ceilings.** The per-agent cost table already says which
   agents would benefit.

