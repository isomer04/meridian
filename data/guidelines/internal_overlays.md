# Internal credit overlays — illustrative lender policy

> **Provenance — read this before quoting anything below.** These overlays are **invented for this
> demo**. They are not any real institution's credit policy, and the file is deliberately named
> generically rather than after a real lender so it cannot be mistaken for one. Every threshold is
> **illustrative and unsourced** (ASSUMPTIONS.md).
>
> What they *are* is structurally faithful: a real lender's overlays sit on top of agency
> guidelines, are **stricter** than agency, and are the thing that actually binds a file. Post-AUS
> this layer is the whole job — **DU can say Approve/Eligible and the overlay can still say no.**
> Reconciling that conflict is `underwriter_agent`'s real task, and it is why the retrieval router
> queries this corpus on *every* file regardless of product: overlays win.

## OV-GEN-01 — Precedence

Where an overlay conflicts with an agency guideline, **the overlay governs**. An automated
underwriting recommendation of Approve/Eligible establishes agency eligibility only. It does not
waive any overlay, and it may not be cited as the basis for exceeding one.

## OV-GEN-02 — Exceptions

An overlay may be exceeded only through a documented exception. An exception requires the
compensating factors named in the specific overlay, an underwriter's written rationale in the
file, and a condition recording the basis. An exception may not be granted on the strength of the
AUS recommendation alone.

## OV-DTI-01 — Maximum debt-to-income, salaried borrowers

Maximum total debt-to-income of **45 percent** for a salaried W-2 borrower, against agency's 50
percent for a DU Approve. No exception above 45 percent.

## OV-DTI-02 — Maximum debt-to-income, self-employed borrowers

Maximum total debt-to-income of **43 percent** for a self-employed borrower, against agency's 50
percent for a DU Approve. Self-employed income is inherently less predictable and the tighter
ratio is the compensating control.

**Exception path.** A ratio above 43 percent and not exceeding **45 percent** may be approved
where **all** of the following are documented:

1. a representative credit score of **700** or above;
2. reserves of at least **six months** of the qualifying monthly housing expense, verified and
   excluding gift funds and the proceeds of the subject transaction; and
3. a minimum of two years of self-employment history in the same line of work.

An approval under this exception must carry a prior-to-docs condition recording the compensating
factors relied upon. A ratio above 45 percent is declined regardless of compensating factors.

## OV-LTV-01 — Maximum LTV, principal residence

Maximum LTV of **95 percent** on a one-unit principal residence, against agency's 97 percent.

## OV-LTV-03 — Maximum LTV, second homes

Maximum LTV of **80 percent** on a second home, against agency's 90 percent. This applies to the
LTV calculated on the **lesser of purchase price or appraised value** per agency B2-1.2-01. Where
an appraisal returns a value that pushes the LTV above 80 percent, the loan amount must be reduced
to 80 percent of the appraised value or the borrower must bring the difference in cash. **There is
no exception path for this overlay** — a second home above 80 percent LTV is declined.

## OV-LTV-04 — Maximum LTV, investment property

Maximum LTV of **80 percent** on a one-unit investment property, against agency's 85 percent.

## OV-SCORE-01 — Minimum representative credit score

Minimum representative credit score of **660** on a principal residence, **700** on a second home,
and **720** on an investment property. Agency permits lower scores with a DU Approve; the overlay
does not.

## OV-VA-01 — Exercising a value acceptance offer

An offer of value acceptance in the DU findings is an **option, not an instruction**. The offer
must **not** be exercised, and a full appraisal must be ordered instead, where any of the
following is present:

1. the property is in a **rural** market or has fewer than three recent comparable sales within a
   reasonable radius;
2. the property is **unique**, non-conforming for its market, or has an unusual site or
   improvement characteristic;
3. the subject is a **second home or investment property**;
4. the property is in an area affected by a **declared disaster** since the data supporting the
   offer was assembled; or
5. the file contains **any indication that the estimated value is unsupported**, including a
   listing history, an appraisal on a prior transaction, or a seller concession pattern
   inconsistent with the stated value.

Where none of these is present, exercising the offer is permitted and preferred: it removes
roughly nine days of appraisal turn time and a $600 borrower cost.

## OV-SE-01 — Self-employment history

A minimum of **two years** of self-employment history, documented with two years of signed
individual returns including all schedules and a year-to-date profit and loss statement. Agency
permits a one-to-two-year history with prior comparable employment; the overlay does not.

## OV-SE-02 — Self-employed cash-flow analysis

Qualifying income must be derived from a cash-flow analysis of the business returns. Where the
adjusted annual figure **declines** between the two most recent years, the **more recent year must
be used** — the two-year average may not be used. A decline exceeding 25 percent between the two
most recent years requires an underwriter's written assessment of business stability before the
income may be used at all.

## OV-RES-01 — Minimum reserves

Minimum reserves of **two months** of the qualifying monthly housing expense on a principal
residence and **six months** on a second home, against agency's two months for a second home.

## OV-CRED-01 — Derogatory credit

A bankruptcy discharged less than **four** years prior, or a foreclosure or short sale completed
less than **seven** years prior, is declined. Agency permits shorter seasoning with documented
extenuating circumstances; the overlay does not.

## OV-COND-01 — Condominium projects

A non-warrantable condominium project is not eligible. A full project review is required on any
condominium regardless of the AUS recommendation, and the review must be in the file prior to
docs.

## OV-DOC-01 — Age of credit documents

Credit documents must be no more than **90 days** old on the note date, against agency's four
months. A re-pull required by this overlay is a deliberate act and must be recorded as a new
attempt against the loan, because it produces a new hard inquiry.

## OV-AA-01 — Adverse action

Where a file is declined, an adverse action notice stating the **specific principal reasons** must
be issued within **30 days** of receipt of a completed application under ECOA and Regulation B.
The reasons stated must be the reasons actually relied upon. A prohibited-basis characteristic may
not appear in the reasons, in the file rationale, or in any input to the decision.
