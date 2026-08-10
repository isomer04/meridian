# Honest limits

- **Single process.** One shared WAL connection, crews run tasks sequentially. Strictly this is
  a durable workflow, not a distributed saga. Same pattern, one process.
- **The citation checker now has a semantic but fallible ceiling.** Deterministic existence and
  lexical checks run first; only a surviving citation reaches the QC agent's entailment task, and
  that task may only downgrade. It catches the tested lesser/greater inversion, but an LLM judge
  can still falsely reject support or miss a subtle contradiction. Stub runs exercise only the
  deterministic floor, and the eval report labels that distinction.
- **The corpora are hand-written paraphrases** prepared for this build, with real-looking
  section numbering so citation verification is exercised realistically. The overlays are
  invented and named generically rather than after any real institution. Citation validity
  measures internal consistency against the corpus, not fidelity to the Selling Guide.
- **Offline embeddings are lexical, not semantic.** With a key, `text-embedding-3-small`; without
  one, a deterministic character-n-gram hash. Genuinely weaker at paraphrase. Which embedder ran
  is recorded in the report so numbers are never compared across the two.
- **No cassettes are shipped.** The record/replay mechanism is complete and sequence-aware, but
  recording needs a key: `run_cli.py --judgment crew --mode record`. Strict replay raises on a
  miss rather than stubbing, because a silently stubbed agent would make every eval number
  meaningless.
- **The cycle-time figures are modeled.** Every baseline is labelled illustrative and unsourced
  in [assumptions.md](./assumptions.md), and the irreducible floor — appraisal turn time, title,
  VOE response, borrower document return — is reported alongside, because naming what does not
  compress is more credible than the reduction.
- **The eight-agent roster is demonstrative, not production.** It is retained to show conditional
  menus and a measurable agent handoff. Production uses three agents; the extra demo roles add
  cost and malformed-output surface without adding independent production judgment.
- **Human approval wait is workload-dependent.** G1–G4 are durable and measured once decided,
  but the one-day queue baseline is illustrative. `--auto-approve` is for demos/evals and is
  named in output and persisted as approver `AUTO-APPROVE`.

← [Back to README](../README.md)
