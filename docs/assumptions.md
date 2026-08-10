# Assumptions

Every number this system reports about *time saved* is modeled, not measured. This file is where
the inputs live so the claim can be audited rather than taken on faith.

**The rule applied throughout: anything not sourced is labelled `illustrative, unsourced`.**
"I estimated this, and here is my logic" survives scrutiny. A fabricated citation does not.

---

## What is measured vs. modeled

| Quantity | Status |
|---|---|
| Agent wall-clock latency per run | **measured** — `time.perf_counter()`, reported per agent |
| Token counts and cost | **measured when live**, from the provider's usage; reported as 0 when no model was called rather than estimated |
| Tool calls made per run | **measured** — the gateway ledger |
| Citation validity rate | **measured** against the shipped corpus |
| Decision agreement across N runs | **measured** |
| Baseline queue days per step | **illustrative, unsourced** |
| Baseline underwriter touch minutes per step | **illustrative, unsourced** |
| Baseline underwriter touches (14) | **illustrative, unsourced** |
| Target underwriter touches (2) | **illustrative, unsourced** |
| Appraisal fee ($600) | **illustrative** — plausible for a conventional SFR appraisal in 2024–25; not quoted from a fee schedule |
| Appraisal turn time (8–14 days) | **illustrative, unsourced** |
| Human approval queue wait (1 day when a gate applies) | **illustrative, unsourced**; actual elapsed wait is also recorded |

---

## Scope of the cycle-time claim

**Application-complete → conditional approval.** Precisely:

- **Starts** when all six pieces of information under 12 CFR 1026.2(a)(3) have been received —
  name, income, SSN, property address, estimated value, loan amount. This is the point at which
  an application legally exists and the 3-business-day Loan Estimate clock starts.

  **On the SSN, precisely.** The regulation's sixth piece is the borrower's Social Security
  number — the full number, or whatever unique identifier the credit report is actually pulled
  against. The scenario fixtures carry `ssn_last4` and a separate `ssn_on_file` boolean, and it
  is **`ssn_on_file` that the completion check reads**. `ssn_last4` is a display surrogate for a
  fixture that should not contain real PII; it is not sufficient to satisfy the regulatory
  test and is deliberately not what the check looks at. A production intake would validate the
  full number against the identifier used for the tri-merge.
- **Ends** at the underwriting decision with conditions issued.

It does **not** include: borrower shopping, pre-approval, condition clearing after the decision,
docs, closing, or funding. A "30 days to close" claim would be a different and much larger
number, and conflating the two would be the easiest way to lose credibility on this.

Measuring from "the borrower first called" would produce a flattering number and a wrong one.

---

## Baselines, per step

From `src/meridian/core/cycle_time.py`. `baseline_queue_days` is calendar time a file waits;
`baseline_touch_minutes` is human time spent. **All illustrative, unsourced.** They are
estimates of a mid-size lender's conventional purchase workflow, chosen to be defensible rather
than impressive — the reasoning is stated so a reader can disagree with a specific figure
instead of the whole model.

| Step | Queue days | Touch min | Compressible | Reasoning |
|---|---|---|---|---|
| `intake` | 2.0 | 45 | yes | A processor keying the file and chasing the six pieces. Mostly waiting on a person to get to it. |
| `disclose_le` | 1.0 | 15 | **no** | A regulatory **deadline**, not a waiting period. §1026.19(e)(1)(iii) requires the Loan Estimate to be delivered *within* 3 business days of application — nothing requires anyone to wait, and this system delivers immediately and records `le_due_on` as the outside date. The queue day is charged because a lender's LE goes out through a compliance review that does not compress; it is not the borrower waiting out a clock. |
| `pull_credit` | 0.5 | 10 | yes | The bureau responds in seconds. The half day is a human getting to it. |
| `verify_income` | 4.0 | 90 | yes | The single biggest touch sink, and the biggest genuine win. |
| `verify_employment` | 3.0 | 20 | **no** | Employer response time. No amount of orchestration moves it. |
| `submit_to_aus` | 1.0 | 25 | yes | DU responds in under a minute; the day is queue, and reading findings is real work. |
| `order_appraisal` | 9.0 | 15 | **no** | AMC turn time. Assigning, scheduling, inspecting, writing, reviewing. The largest single non-compressible block, and the reason value acceptance matters so much. |
| `guideline_research` | 3.0 | 75 | yes | Overlay reconciliation. Currently a person reading policy documents. |
| `underwrite` | 5.0 | 120 | yes | Queue for an underwriter, then the review. |
| `compliance_qc` | 1.5 | 40 | yes | Pre-decision QC. |
| `approval_wait` | 1.0 | 10 | **no** | Named human decision at G1–G4. Applied only when a gate is triggered and a nonzero wait is recorded. |

**Totals before a human gate applies:** a full file with an appraisal is ~30.0 queue days and
~455 touch minutes; value acceptance is ~21.0 days. Add the modeled 1.0-day approval queue and
10 touch minutes for a gated path. Auto-approved demos record zero elapsed queue time.

---

## How the modeled number is derived

```text
modeled_days = irreducible_floor_days + (measured agent wall-clock in days)
```

rounded **up** to the next half day, floored at 0.5, because a lender's day is a business
day. Up rather than to-nearest: rounding to nearest could report a modeled figure below
the irreducible floor it is built on, and rounding a headline number in the flattering
direction is the specific thing not to do here.

`irreducible_floor_days` is the sum of `baseline_queue_days` for steps marked
**not compressible**. So:

- **With a full appraisal:** floor = 1.0 (LE) + 3.0 (VOE) + 9.0 (appraisal) = **13.0 days**.
  Baseline for that same path is 30.0. So **30 → 13**.
- **With value acceptance exercised:** floor = 1.0 (LE) + 3.0 (VOE) = **4.0 days**. Baseline for
  that path is 21.0. So **21 → 4**.

These are the figures the code actually prints; `run_cli.py --scenario 1` and `--scenario 3`
show them.

### The headline number, and the problem with it

**"30 days → 5 days" compares two different paths**, and that is worth saying out loud before
anyone else does. The 30 is the appraisal path; the 4–5 is the value-acceptance path. Compared
like-for-like it is **30 → 13** or **21 → 4**, and neither of those is 30 → 5.

The defensible framings, in order of how well they survive scrutiny:

1. **"Underwriter touches from ~14 to ~2 on a clean conventional purchase."** Path-independent,
   and the thing an operations leader actually manages.
2. **"21 days to 4, on a file that receives and exercises value acceptance."** Same path, stated
   condition.
3. **"30 days to 5."** Only true if the baseline file needed an appraisal and the new one does
   not — which is a real and common improvement, but it is partly a *product* change (using value
   acceptance) rather than an *orchestration* one, and it should be attributed accordingly.

Lead with (1). Use (2) when a number of days is wanted. Use (3) only with the condition attached.

A file that needs a full appraisal **cannot beat the appraisal**, and no amount of agent
orchestration changes that.

The model assumes agent work replaces queue time on compressible steps entirely. That is
**optimistic** and it is the assumption most worth arguing about: in practice you would keep a
human review step, so a realistic figure sits above the modeled one.

---

## What does not compress, restated

Naming the floor is more credible than the reduction, and it pre-empts *"so why not one day?"*
Each item below says which modeled step carries it, so the list and
`irreducible_floor_days()` describe the same thing rather than drifting apart.

| Real-world constraint | Where it lives in the model | In the floor? |
|---|---|---|
| **Appraisal turn time** — a person drives to a property | `order_appraisal`, 9.0 queue days, `compressible=False` | yes, unless value acceptance is exercised |
| **LE compliance review** — the disclosure goes out through a control | `disclose_le`, 1.0 queue day, `compressible=False` | yes |
| **VOE response** — an employer's HR department | `verify_employment`, 3.0 queue days, `compressible=False` | yes |
| **Borrower document return** — the borrower has to find last year's tax return | absorbed into `verify_income`'s 4.0 queue days, which is marked **compressible** | **no** |
| **Title / settlement** | not modeled | **no** |

The last two rows are where this model is optimistic, and they are stated rather than
buried:

- **Borrower document return is inside a step the model treats as compressible.** Agents
  genuinely compress the *lender's* half of `verify_income` — the calculation, the
  escalation, the second look — but they do not make a borrower find a tax return faster.
  Treating the whole 4.0 days as collapsible therefore overstates the saving on a file
  where documents are outstanding. Splitting it into a borrower-wait baseline and a
  lender-work baseline is the honest next version; it is not done here, so the modeled
  number should be read as a floor on the achievable time, not a forecast.
- **Title is out of scope**, not free. The scope is application-complete → *conditional
  approval*, and title work is not on that critical path — it binds before closing, which
  is a different and larger number this model deliberately does not claim.

Of the three constraints that are in the floor, only the appraisal is avoidable at all, and
only when DU offers value acceptance and the file is clean enough to exercise it.

---

## Touches: 14 → 2

**Both illustrative and unsourced.** 14 is an estimate of the distinct occasions an underwriter
or processor picks a conventional purchase file up: intake review, doc chase, income
calculation, self-employed escalation, credit review, liability questions, AUS submission,
findings review, appraisal review, guideline questions, overlay questions, condition writing, QC
back-and-forth, and final sign-off.

2 is the target: **one** substantive review of the assembled recommendation, and **one**
sign-off. Every other touch above is either automated here or eliminated by the file arriving
assembled.

Lead with touches. It is the number a lending operations leader actually manages, it is less
sensitive to queue-time guesses, and it maps directly onto what the agents do.

---

## Domain conventions used

Not assumptions so much as decisions about what to model, recorded because a home-lending
reviewer will check them:

- Credit is a **tri-merge**; the representative score is the **middle of three**; on a joint file
  the loan takes the **lower** of the borrowers' representative scores.
- A duplicate mortgage inquiry is deduped for *scoring* inside a 14–45 day rate-shopping window,
  but **the inquiry still appears on the report**.
- **Value acceptance**, not "appraisal waiver" — Fannie removed that term from the Selling Guide.
  It is **Fannie's offer, issued through DU**, not something a lender derives from its own LTV.
- Purchase LTV uses the **lesser of** sales price or appraised value.
- Conditions are **PTD / PTF / PTC / at-close** — four categories.
- Adverse action under ECOA/Reg B is due within **30 days** of a completed application.
- Recommendation and eligibility from DU are **independent**; Approve/Ineligible is a real
  combination and is not collapsed.

---

## Fixture and corpus provenance

- **Vendor responses are seeded fixtures** in `data/scenarios/*.json`. No live vendor is called.
- **The guideline corpora are hand-written paraphrases** prepared for this build, using
  real-looking section numbering so citation verification is exercised realistically. Each file
  carries a provenance header. Do not quote them as the Selling Guide or as 4000.1.
- **The internal overlays are invented.** They are named generically rather than after any real
  institution, precisely so they cannot be mistaken for a real lender's credit policy. They are
  structurally faithful — stricter than agency, and binding — which is what makes them useful
  for the demo.
- **Borrowers, employers and properties are fictional.**

