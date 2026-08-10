# Reliability

## The Reg Z control is mechanical, not a prompt

Under TRID/Reg Z **§1026.19(e)(2)(i)(A)** a creditor may not impose *any* fee before the
consumer has received the Loan Estimate and indicated **intent to proceed**. There is exactly
one exception, **§(e)(2)(i)(B)**: a bona fide credit report fee.

```python
@requires_state(intent_to_proceed=True)     # order_appraisal — $600, gated
# pull_credit                               — NOT gated; §(e)(2)(i)(B) exception
```

The $600 order is **mechanically impossible** until the precondition is in state, and the
failure is a `PolicyViolation` that propagates and aborts the task. That distinction is the
whole point: a refusal returned as *text* is a suggestion an agent rephrases its way around.

`run_cli.py --scenario 1 --policy-attack` injects an instruction to expedite by ordering the
appraisal early. The control holds. `tests/test_core.py` also asserts that `pull_credit` does
**not** carry the decorator, so a well-meaning future edit "adding the missing gate" fails the
suite.

## The idempotency key does not depend on model output

```
key = sha256(loan_id | saga_step_name | attempt_epoch)
```

An earlier design hashed the tool arguments. But arguments come from an LLM, so a retry
emitting `"123-45-6789"` and then `"123456789"` yields a different key — and **a second hard
inquiry**, on exactly the path the guarantee exists to protect. `attempt_epoch` increments only
when the orchestrator *deliberately* intends a fresh call (credit ages out at 120 days).

Tested in both directions: argument drift must not re-fire, and a deliberate epoch bump must.

## Compensation, including the parts that cannot compensate

| Step | Execute | Compensate |
|---|---|---|
| `pull_credit` | hard inquiry (tri-merge) | ⚠️ **none possible** — the inquiry is permanent |
| `submit_to_aus` | DU findings | none needed (read-only) |
| `order_appraisal` | $600 to the AMC | cancel — **refundable pre-inspection only** |
| `lock_rate` | pricing exposure | release — **relock at worst-case pricing** |

Two rows deliberately do not restore prior state, and both are kept rather than quietly
dropped, because that is the actual lesson: **a saga is not a rollback, it is a sequence of
business-level apologies, and some apologies cost money.** In scenario 3 the appraiser has
already been out, so the ledger records $600 *not* recovered.

## `meridian.db` is the only source of truth

CrewAI's `@persist()` writes flow state to its **own** SQLite database with its own commit
boundary — so it is treated as a cache. Authoritative state is written to `loan_state` inside
`transaction()` at every step boundary, and any loan is reconstructible from `saga_log` alone.

Demonstrated, not asserted: kill the process mid-flow and re-run. Completed steps rebuild from
the ledger and the credit bureau is **not** called again.

← [Back to README](../README.md)
