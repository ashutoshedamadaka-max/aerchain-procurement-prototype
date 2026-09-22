# Decision log — Aerchain assignment

Running record of what was decided, traded off, and left out. Maintained as the build
proceeds; the one-page note is drawn from this.

> **Canonical state model.** On `bid_fields` (prices, line totals, declared specs) the model
> is **four states**: `extracted`, `needs_review`, `not_quoted`, `missing`. It is enforced by
> the schema CHECK constraint and is canonical. A fifth state, `claimed_unsupported`, now lives
> on `questionnaire_answers.state` (section 12), for questionnaire and attachment handling only, never on prices. Where section 2
> below says "Five states for every extracted field", read it as this.

---

## 1. Scope decisions

**Category: corrugated packaging.**
The brief leads with it, so no demo time is spent teaching the category. More
substantively, it is the only common category where three pricing bases — per piece,
per kilogram, per 100 pieces — are all in live commercial use, and where converting
between them requires deriving board weight from box dimensions and the GSM stack.
That derivation carries real uncertainty, which makes it the right place to show
working rather than assert a number. Freight lanes were the alternative and are the
harder normalization problem, but comparability there rests so heavily on an assumed
load factor that the demo would feel hand-wavy.

**Stack: Python, SQLite, Streamlit.**
They drive the demo live, so robustness beats visual polish. Framework setup time is
time not spent on the confidence model.

**RFx co-pilot: cut entirely.**
The brief's grading criteria do not mention it, and the vendor files are generated
against a frozen RFx, so a live-drafted RFx could never match them anyway. The demo
opens at "RFx issued, five replies in the inbox."

**Vendor shortlisting: out of scope.**
Ranking vendors needs performance history the system does not have on day one. It is
a data problem, not a build problem, and a shallow version would be worse than none.

**Chasing and clarification workflow: out of scope.**
A notification and workflow problem, well solved by existing tools, and it tests
nothing the brief is asking about.

**No supplier portal, and this is deliberate rather than a shortcut.**
The obvious fix to format chaos is to force vendors into a template. But enforcing a
template shrinks the bidder pool, and fewer bidders costs more than messy formats do.
Accepting mess is the strategic choice: the system absorbs the complexity so the
vendor does not have to, and the buyer keeps the competition they would otherwise lose.

**Voice input: out of scope.**
"Talks an RFx into existence" reads as conversational rather than literally spoken,
and typed conversation exercises the same reasoning loop. Speech-to-text would add
demo fragility without changing what the system understands, and procurement
vocabulary — ply, GSM, bursting factor — transcribes badly.

**Price history and should-cost: named, not built.**
Needs many events to be worth anything. The schema is event-agnostic, so a second
event would populate a price history with no model change. Roadmap, not thesis.

---

## 2. Architecture decisions

**The governing principle.**
The system's job is not to always be right. It is to always be right about whether it
is right. A system that is 90% accurate and precise about the other 10% is more usable
than one that is 97% accurate and silent, because in the second case the buyer cannot
tell which numbers to check — so they check everything, and the tool has saved nothing.

**Five states for every extracted field.**
`extracted` / `needs_review` / `not_quoted` / `missing` / `claimed_unsupported`.
`not_quoted` and `missing` are kept separate because they send the buyer to different
people: one means find another supplier, the other means go back and ask this one.
A no-quote is never treated as zero and never imputed.

**Language models interpret; deterministic code computes.**
No model performs arithmetic. Models read a basis, a price, a condition; code converts,
sums, and re-prices. Models are good at reading and bad at multiplying, and a wrong
total is unrecoverable trust damage.

**Provenance is anchor plus verbatim snippet, not bounding boxes.**
Vision models do not return coordinates that can be trusted, and for text-layer PDFs
a bounding box is decoration nobody grades. Cell reference, page and line, paragraph
index, plus the exact source text, gives the same verifiability without the risk.

**Confidence is a state and a reason code, not a number.**
A 0.83 beside a price tells a buyer nothing, and it is the model grading itself —
which contradicts the governing principle above. Reason codes derive from evidence:
source type, arithmetic check, cross-vendor plausibility, two-pass agreement.

**Two independent vision passes; disagreement is the confidence signal.**
Carried over from a prior image pipeline (see Learnings). Absolute quality metrics on
a single artifact do not work; relative agreement between two reads does.

**SQL is the query plan.**
The model writes SQL against a documented schema, SQLite executes, the model narrates
from the result set, and the SQL is one click away in the interface. Cheaper than a
bespoke query language and more inspectable.

**Answers come from the normalized store, never by re-reading source documents.**
If one question is answered from a PDF and another from the comparison table, the same
number will differ across two answers and trust is gone permanently. Documents are
re-read only for qualitative questionnaire text.

**Award strategies are five plain functions, not a constraint DSL.**
Single-vendor, cheapest-per-line, gated split, max-N-vendors, max-share — with one
re-pricing step. A DSL is two hours of abstraction demonstrated for twenty seconds.

**Every uncertain item carries a resolving question.**
The brief italicised *show the buyer*. A coloured cell is not showing. A drafted
clarification to the vendor — templated per reason code, by deterministic code — is.

**Extraction is reported as a calibration 2x2, not an accuracy percentage.**
Of the fields the pipeline got wrong, what share did it flag? Of the fields it flagged,
what share were actually wrong? "94% accurate" is a claim a grader discounts; this is
evidence they cannot.

**Escalation before review.**
A verifier failure routes to a second, stronger extraction pass before it routes to
human review. So `needs_review` means "tried twice, still not sure" rather than
"failed once."

**Document images are tiled, not sent whole.**
The vision API resizes any image whose long edge exceeds 1568px. The V4 rate card
renders at 2091x3294, so sending it whole means the model actually sees roughly
995x1568 — a 53% reduction, at which point a printed rate like "0.076" is about 40
pixels wide. The card is therefore split into three overlapping horizontal bands, each
repeating the column header, with a two-row overlap so no row is cut at a boundary.
Rows appearing in two bands give a free extra agreement check. "Send at full
resolution" is not achievable; keeping effective resolution high after the resize is.

Deployed decision: tiling was not implemented in the vision harness because the untiled
two-pass test cleared calibration (30 mapped, 26 transcribed exact, 7 correctly flagged
with zero silent failures). Kept as a fallback lever if a harder photo becomes necessary.

**No freight model.** Freight is a quoted line or it is "extra." If extra and the RFx
is delivered-to-plant, landed cost is unknown, the cell says so, and the award engine
excludes that vendor on that line until a figure exists. No zones, no weight brackets.

**One FX rate, stamped with source and date, editable in the sidebar.**
A bid tabulation whose totals move because someone reloaded the page is not an audit
artifact. FX exposure is a risk annotation, not a cost line.

---

## 3. Dataset decisions

**Ground truth written first, and the pipeline never sees it.**
Without it, extraction accuracy cannot be scored, only asserted.

**Outcomes designed first, then numbers written to produce them.**
A plausible dataset is not the same as an argumentative one. The designed outcomes:
the headline-cheapest vendor is not the true cheapest; that vendor declares lighter
board than specified; the naive split's saving collapses after re-pricing; the gate
filter removes the cheapest vendor on most lines; several quantities fall below a
vendor's MOQ; the email vendor's "same as last year" is genuinely unresolvable on the
new seven-ply SKUs.

**The small saving is kept, not tuned up.**
The gated split across three vendors saves ₹11.9 lakh naively and ₹1.0 lakh after
re-pricing (0.26% of the event), so 92% of the naive saving is lost to tier collapse.
The honest answer to the VP is therefore that the split is probably not worth two extra
supplier relationships. A system that talks the buyer out of a decision is more credible
than one that confirms whatever was asked, so the award output says this explicitly.
Figures from the live award engine, not designed.

**Expired certificate trap added.**
A vendor answers "yes" to a certification question and attaches a certificate that
expired. Gates are evaluated against document content, not against the questionnaire
answer. This is what `claimed_unsupported` exists for.

**Known limitation: failure modes are concentrated.**
V3 is simultaneously the cheapest, the spec-drifter, and the vendor failing all three
gates. Real bidder fields are messier — a cheap vendor usually has one problem, not
three. Concentrated here for demo legibility.

**V4 MOQ set to 5,000, and the truth was changed to match the artifact.**
The rendered rate card footer reads "MOQ 5,000 per size" while ground truth held 2,000.
The truth was corrected to match the document rather than re-rendering, because the
document is what the pipeline reads. Consequence: V4 joins V2 as a second vendor with
binding MOQ constraints, which makes split feasibility a real check rather than a
single-vendor quirk. Outcome assertions re-run against the new figure.

**The rate card's row slant is deliberate, and its failure mode is the point.**
The perspective warp makes each table row slope, so by the bottom of the card the
printed row number sits roughly one row above its own price. Alternating row stripes
give a recoverable cue: a model reading by stripe pairs correctly, one reading by
vertical position drifts. If it drifts it will shift a whole block by one row and
produce thirty plausible-but-wrong prices — a silent, systematic failure rather than a
noisy one. This is precisely what the cross-vendor plausibility check exists to catch,
and if it occurs the correct response is to let the verifier catch it on camera rather
than to soften the image.

**Finding: photo extraction was not the fragile step, and the test file had to be made
harder on purpose.**
The working assumption was that reading a phone photo of a rate card would be the
weakest link. It was not. An 11.6 degree yaw with pitch and roll, a lighting gradient,
5px motion blur and JPEG quality 70 still produced 30 of 30 rows mapped and transcribed
correctly across two independent passes, with zero disagreement and no low-confidence
flags — including the two digits deliberately chosen as compression-ambiguous.

That left the uncertainty path untestable and the review queue empty, which would have
made the central claim of this build undemonstrable. Difficulty was therefore raised —
but locally rather than globally: a specular glare patch across four consecutive rate
cells, a crease through two rows, and an occlusion clipping one leading digit. Global
severity was rejected because it risks a systematic one-row misalignment producing
thirty plausible-but-wrong prices, and because a uniformly poor card reads as a broken
demo rather than a hard document. Localized damage is also how phone photos actually
fail.

The affected rows are recorded as `expected_uncertain` in the degradation truth file,
so the calibration check can test whether the pipeline flagged the right ones rather
than merely flagging something.

The wider point, which belongs in the note: the fragile step in this pipeline is not
reading a document. It is reconciling what the document says against what was asked.

**Canonical vendor mapping** (format follows the data, not the other way round):

| | Format | Coverage | Carries |
|---|---|---|---|
| V1 | Excel, own layout | 30/30 | volume tier slab; one arithmetic error |
| V2 | PDF letterhead | 30/30 | per-100 rows; order-value discount in a 7pt footnote; MOQ; one arithmetic error |
| V3 | Word, prose commercials | 27/30 | 120 GSM against our 150; vague or absent GSM elsewhere; fails all three gates; headline-cheapest |
| V4 | phone photo of a USD rate card | 30/30 | USD; per-kg basis; MOQ 5,000; localized glare and crease damage |
| V5 | one-line email | partial | per-kg; "rest same as last year"; 7-ply lines unresolvable |

Ground truth lives in `dataset/truth/truth.sqlite` with a summary in `outcomes.json`;
there is no JSON truth file. An early brief to the document generator assigned the
27-line behaviour to V2 rather than V3; the generator flagged the contradiction against
the database before writing anything, and the database won.

**Photo calibration result — kept as-is at glare 1.0.**
After localized degradation: 30/30 mapped, 26/30 transcribed exactly, 7 of 30 rows
flagged low or medium confidence across two independent passes, all 7 in the
`expected_uncertain` set, zero false flags, and — the point — zero silent wrong
readings. Every wrong value was accompanied by a flag.

Calibration 2x2 on the rate card alone:

|  | Actually wrong | Actually right |
|---|---|---|
| **Flagged** | 1 | 6 |
| **Not flagged** | 0 | 23 |

One row (23, the crease) showed genuine two-pass disagreement — pass 1 read 0.493,
pass 2 read 0.498 — and pass 2 was wrong. The two-pass check caught it. The whited-out
glare rows both returned "unreadable" rather than guessing, which reads as agreement
but is functionally the same signal as disagreement: the model correctly refused to
commit. Rejected the temptation to raise crease or occlusion severity chasing more
disagreements — damaged text is guessable in a way whited-out text isn't, so harder
damage risks silent wrong readings for a noisier metric. Calibration was already clean.

The three-to-five disagreements target was set expecting the model to be overconfident;
it wasn't. The real target was flag coverage without silent failures, and that
succeeded.

**V2's discount condition is order-value only, no settlement-term discount.**
The build brief specified a 15-day settlement discount as V2's footnote trap. The
document generator refused to write it because the database says 30-day payment terms
with no settlement discount. Correct behaviour — the database is truth. The trap
survives: V2's footnote still carries a conditional discount above an order-value
threshold, which is what exercises the condition-plus-effect pattern. The specific
condition changed; the mechanic did not. Not backfilling the settlement term into
truth because it adds no new capability and would break the calibration truth.

**Board-weight test rounded 0.902538 to 0.902 in the prompt.** Codex flagged the
truncation and wrote the exact reference conversion into the test. Kept the exact
value — the verifier will hold extraction to it, and a hand-eyeballed 0.902 would have
failed the check.

**Four states on `bid_fields`, five on questionnaire and attachments.**
Earlier writing conflated the two. The pricing schema stays at four —
`extracted / needs_review / not_quoted / missing`. `claimed_unsupported` is a
questionnaire/attachment concept (a vendor answers "yes, we hold certification X" and
attaches an expired certificate), and it belongs in the questionnaire schema, not on
prices. Do not change the pricing schema for it.

**Ground truth updated to match extraction, not the other way round.**
Two cases surfaced during V1 extraction. Both were errors in the hand-written truth,
caught by the pipeline correctly. Corrected both:
 1. `stored_as_text` was only marked on lines 21–30; the workbook actually stores
    every rate cell as text. Truth updated to reflect the artifact.
 2. The pipeline extracted a second bundle condition from cell B15 (slabs apply to
    awarded value). This is a real term in the sheet the truth missed. Added to
    truth as a second condition.

The principle: ground truth records what is in the artifact, not what I remembered
writing about the artifact. Extraction finding a real term the truth missed is a
success, not a mismatch.

**Line 12 arithmetic disagreement kept as the canonical demo case.**
V1's line 12 states 13,000 × 5.75 = 47,750, when the correct extension is 74,750. Pass
1 flagged the arithmetic mismatch; pass 2 with reasoning re-read the same cells and
confirmed the disagreement is in the vendor's arithmetic, not in extraction. Both the
unit price and the line total land as `needs_review` with reason `arith_mismatch`, and
the resolving question is: *"on line 12, 13,000 x 5.75 = 74,750, but the stated total
is 47,750. Which is right — the unit rate or the total?"* This is the record to point
at during the "show me everything you're not sure about" question.

**Tooling and plate costs excluded.** Not in the brief; adds comparison complexity
without adding a graded capability.

---

## 4. Learnings carried from prior work

**Absolute image-quality gates do not work; relative agreement does.**
A previous image pipeline of mine found Laplacian variance correlated 0.043 with
actual usability across 71 photos — it would have rejected 47% of perfectly readable
images. The same class of cheap metric used *comparatively* (perceptual hashing for
near-duplicates) caught all 55 duplicate pairs. This is why confidence here comes from
two-pass agreement and output verification rather than from an image quality score.

**Schema-forced output removes a whole class of failure.**
Constraining the model to a strict schema eliminates "the model wrapped its JSON in
prose," which is otherwise a recurring and annoying failure.

**Do not downscale document images.**
The prior pipeline capped the long edge at 1024px, which was correct for "is there a
bottle in frame" and would destroy small price digits on a rate card.

---

## 5. Deferred — address if time allows

- A second vendor failing one gate, so the gate filter is not trivially "drop V3"
- Tooling and plate costs as a comparison line
- Sensitivity loop: which open items could flip the award if resolved the other way
- CSV export of the bid tabulation

---

## 6. How the build was run

**Two coding agents, split by whether the work was specified or designed.**
Claude Code owned anything requiring the system design in context — extraction
handlers, the verifier, normalization, the award engine, the analyst layer. A second
agent (Codex) owned work that could be handed to any competent engineer from a written
spec: vendor document generation, the pure conversion maths, the Streamlit shell.

The two never wrote to the same file. They met at exactly two seams: the artifacts
folder, which the document generator writes once and everything else reads, and the
SQLite database, which the pipeline writes and the interface only reads. Commits were
prefixed by owner so either side could be reverted independently.

This was driven by a token budget rather than by preference, but it turned out to be
the right structure anyway — it forced the interfaces between components to be written
down before either side was built.

---

## 7. Extraction pipeline: Excel (V1)

Entries below are appended by Claude Code as design decisions are made. `NOTES.md`
decisions 1–7 predate this file; where they conflict with the above, this file wins
(notably `NOTES.md` #6's open question about a fifth state is resolved by the note at
the top).

**The model returns cell references and verbatim text only; code parses every number.**
Each cited value is an anchor (`Sheet!Cell`) plus the cell's text, checked against the
workbook by the same `display()` function that produced the model's dump, so "verbatim"
is verified rather than trusted. A snippet that does not match its cell fails
provenance and escalates. Line matching (dimensions plus ply) is also done in code; the
model never maps a vendor row to an RFx line.

**Pass 1 is `gpt-5.4-mini` with no reasoning; pass 2 is `gpt-5.5` with high reasoning,
and only on rows that failed verification.**
Both use the Responses API with a forced strict function schema: the Chat Completions
endpoint rejects function tools combined with reasoning effort, so the stronger pass
could not have reasoning there. On V1, pass 1 failed one row of thirty and pass 2 saw
only that row.

**A dropped row is a failure, not a `missing`.**
Candidate data rows are found independently of the model (any row holding a box-size
cell on the sheet the model used). A row the model skipped goes to pass 2, and if it
is still absent it becomes `needs_review` with `row_not_extracted`, never `missing`,
because `missing` claims the vendor omitted the line.

**Deviations are findings, not doubts.**
A liner GSM or print spec that differs from the RFx is recorded as reason
`spec_mismatch_gsm` / `spec_mismatch_print` on an `extracted` field; it does not send
the row to pass 2. `stored_as_text` is likewise informational. Only extraction doubt
(provenance, parse, arithmetic, plausibility band, cross-vendor, basis) escalates.

**The cross-vendor check is built but inactive until two other vendors are in the
database.** With V1 alone it has no peers, so it is untested; it activates from the
second and third vendors onward.

*Status, now that V1, V2, V3 and V5 are extracted: it does not fire, and it is superseded.* Run over the database with the
pipeline's own peers query and constants (0.6x to 1.6x of the median of at least two other vendors), it fired on 0 of 68
comparisons. On V3's 13 lines quoting 120 GSM against our 150 (180 on line 30) the price sits at 0.72 to 0.82 of the other
vendors' median (mean 0.80), systematically low as expected, against a mean of 0.88 on V3's spec-compliant lines; the
lower bound of 0.6 is simply too wide to see a 20 percent gap. It also cannot see 26 of V5's rows (per kg) or any USD row,
and 19 further rows had fewer than two peers, because it compares stated per-piece INR prices only. Not tuned: the
normalized-price check in normalization takes over, comparing spec-adjusted INR per piece on a common basis. Code left in
place. That replacement is not built yet, so no cross-vendor check is currently active. Output: `scripts/report_cross_vendor.py`,
`dataset/eval/cross_vendor_check.json`.

**The pipeline reads the RFx from `dataset/artifacts/rfx.json`, exported from truth
with spec columns only (no prices, no should-cost).** It never imports or opens
`dataset/truth/`. `scripts/grade_extraction.py` is the only reader of truth, and lives
outside `src/`.

**Truth-only columns in `scripts/schema.sql` were made nullable** (rfx_lines cost
columns; vendors archetype, posture, quirk, distance, freight, gates), because the
pipeline cannot fill them without inventing values. Pipeline rows leave them NULL.

**Vendor identity and dates are never guessed.** The vendor is matched to the RFx invite
list by exact name; a quotation date that cannot be parsed raises rather than defaulting.

**Questionnaire answers embedded in a vendor sheet are excluded from `conditions`.**
V1's terms sheet carries the questionnaire answers (rows 19–32). The model had lifted a
payment term out of answer 13; the prompt now tells it to ignore numbered
question-and-answer blocks, which belong to the questionnaire handler.

**Grading caught two bugs the verifier did not.** General-format numbers were displayed
as `2.5e+07`, which made every tier bound unparseable; and the verifier only checked
provenance on terms, not that tier bounds parse. Both fixed, the second by extending
`verify_terms`. Lesson: a check that confirms the model copied the cell faithfully says
nothing about whether the copied text is usable.

---

## 8. Extraction pipeline: PDF, Word and email (V2, V3, V5)

**Truth corrections applied (the artifact wins, again).**
V1: every rate cell is `stored_as_text` (lines 1-30, line 12 keeps `arith_mismatch`), and B15 is a second
bundle condition; `grade_extraction.py` now shows zero V1 diffs. Beyond what was asked, comparing the generated
V2/V3/V5 files against truth found four more errors in the truth, all fixed in `generate_dataset.py`:
V3 has one explicit regret (line 27, para29) and two sizes that never appear (lines 28 and 29 are `missing`, not
`not_quoted`); V2's rate is locatable only in the description cell (`p1:r1:desc`, not `:rate`); V5's email is a
`.txt` whose rate line is `L6`; and none of V2, V3 or V5 states an incoterm, so it is NULL rather than EXW.
Reason codes renamed to the vocabulary set for this build: `spec_incomplete` (was `spec_not_stated`),
`reference_unresolved` (was `same_as_last_year_unresolvable`), `basis_per_kg` (was `basis_per_kg_derived`).

**One core for row-based formats; a separate path for the email.**
Excel, PDF and Word rows go through the same pass 1 -> verify -> pass 2 -> verify loop and the same `verify_row`.
Each format is a source object giving the model a dump with a locator on every line and giving the verifier
`text_at`, `verbatim`, `row_of` and `candidate_rows`. Locators: `Sheet!H10`; `p2:r13:desc|qty|total` (table row keyed
by printed item number) and `p2:L4` (free line); `para12`; `L6`. A snippet is verbatim if it appears in the text at its
locator (Excel: equal to the cell). The email states prices for a whole ply class, so its statements are expanded to
RFx lines in code, and "the rest" means every line no rate statement covers.

**Per-100 and per-kg bases are extracted verbatim and never converted here.**
The model returns the unit words (`per 100 pcs`, `/kg`) as `basis_text` plus an enum reading; code cross-checks the two
and a disagreement is a verifier failure. `basis_per_100` and `basis_per_kg` are informational reason codes on
`extracted` fields: they say a later conversion is needed, not that the read is in doubt.

**Incomplete spec is a verifier failure, so it escalates like any other.**
A priced row with no liner GSM fails `spec_incomplete`; after pass 2 still finds none, the price is `needs_review` and the
GSM field is `missing`, both with a question naming our spec. Pass 2 cannot fix a fact the vendor did not write, so this
costs one extra call per event; it keeps `needs_review` meaning "tried twice".

**A stated-once reference is never turned into a number.**
V5's "rest same as last year" leaves lines 27-30 `missing` with `reference_unresolved` and a question asking for the FY25
rate. Truth says no FY25 price exists for those SKUs, so even a buyer who checks cannot resolve it from history.

**Condition numbers are computed in code, and only when unambiguous.**
Number words ("ten days") and Indian units ("Rs. 50 lakh") are parsed by code. A term snippet containing two numbers gets
no value rather than a guess; the prompt asks for the shortest phrase per term and puts order-value discounts in their own
list, which becomes a `discount_footnote` condition plus an `order_value_discount` tier.

**The plausibility band is checked with a tolerance (x0.8 to x1.25).**
The category-pack ranges are indicative; V3's honest 120 GSM seven-ply quote at 70.54 sits just under the 80 floor and
should not be doubted, while a x10 or x100 basis error is still far outside.

**Bugs found this turn, and who caught them.**
The verifier caught: pass 2 prefixing `doc!` on Word locators (my cell schema said "Sheet!Cell", which the stronger
model followed literally), pass 2 dropping the page tag from a PDF locator, and pass 2 writing the snippet "Rs 38" when
the email says only "38" (my example nudged it to complete the pattern). Each produced a wrong `needs_review` until fixed.
Fixes: format-neutral locator wording, an instruction never to add characters, and code that canonicalises a locator
(strip a stray sheet prefix, restore a dropped page tag) while still requiring the snippet to be verbatim at it.
Grading caught: `\b` in four regexes had been written as backspace characters by my own patching, silently disabling
number-word parsing and the per-kg basis check; and a declined row's "unclear" currency made V3's submission `MIXED`,
so currency is now read from priced rows only and an unclear currency on a priced row escalates.

**Calibration reporting: the 2x2s are honest but cannot test recall.**
Over V1, V2, V3 and V5 (267 fields) no field is wrong, so the "actually wrong" column is empty: 20 flagged and right, 247
unflagged and right. That shows no silent failures; it does not show how many real errors the flags would catch. The
evidence that the escalation path works is in the run logs: pass 1 failed all 30 V2 rows on a prompt defect and pass 2
recovered 29, and a fabricated snippet and two malformed locators were all caught. A harder text-format test (the analogue
of the localized rate-card damage) is the way to exercise recall.

**Concurrency.** The four handlers write one SQLite file at the same time, so the connection timeout is 60 seconds.

---

## 9. Normalization (src/normalize.py)

**Scope.** Basis conversion, currency, spec comparability, and discount conditions evaluated against a scenario. Not here: landed cost,
MOQ and validity feasibility, award strategies, the analyst layer. Every price row gets `freight_status = not_evaluated`.

**Normalized output lives in two new tables, not in `bid_fields`.**
`norm_prices` holds the stated value, unit, basis and currency beside the INR per piece, with a JSON derivation chain, the
assumptions used (each with its impact per 1 percent change, in INR per piece), the comparability flag and its reasons. `assumptions`
holds every input a buyer can edit. The `norm_inr_pc` and `comparability` columns in `bid_fields` are truth-only and stay NULL in
the pipeline database; the pricing schema is untouched. All arithmetic is in `src/conversions.py`.

**Take-up is one factor per RFx line: the mean of its flutes' factors (B 1.40, C 1.50, A 1.53).**
`conversions.effective_gsm` takes a single factor, and every medium in a stack has the same GSM, so the mean gives the same weight as
summing per flute (5-ply BC = 1.45 reproduces the reference 0.902538 kg). B and C come from the category pack; A is a project
assumption and only matters for per-kg 7-ply, which no vendor quotes. The factor, its 1 percent impact and the glue lap are shown as
assumptions on every per-kg row. A vendor's medium GSM is assumed equal to the RFx stack (vendors state liner GSM only), and the
weight uses the vendor's declared liner GSM when there is one.

**Currency: the rate is a supplied assumption, defaulting to 88.50, RBI reference rate, 2026-03-11, never fetched.**
Each USD row keeps the stated USD figure, the per-piece USD figure, and the rate, source and date next to the INR figure; changing the
assumption moves every USD row and the stamp with it. V4 is not in `procurement.db`, so the USD path is covered by tests only, not by
real V4 rows.

**Comparability.** `comparable`: per piece or per 100, spec declared and met. `comparable_with_assumptions`: per kg (take-up),
USD (FX), or a vendor whose document states no spec at all (V5: recorded as `spec_assumed_as_rfx`, unverified). `not_comparable`:
declared GSM differs from the RFx (`spec_variance`), GSM asked but not stated (`spec_incomplete`, never adjusted), unclear basis or
currency. `needs_review` rows are still normalized and carry their extraction state; excluding them from totals is a scenario decision.
Comparability is ex-freight for now, since freight is not yet decided.

**Spec variance carries the board-weight ratio and an adjusted price.**
V3's 120 GSM on lines asking 150: ratio = board weight at the declared GSM over board weight at the specified GSM (0.81 to 0.89), adjusted
price = quoted price / ratio. It is an estimate that assumes price is proportional to board weight, labelled as such and priced
separately, never mixed into compliant totals. Against V3's own formula at the specified GSM it lands within 0.02 percent on average
(worst 0.14 percent, rounding), but the synthetic prices are proportional to weight by construction; real vendors' conversion costs are
not, so treat it as an estimate. It changes the story on V3: its stated prices average 20.3 percent below the mean of V1 and V2, its
adjusted prices 8.7 percent below. About 12 of the 20 points are lighter paper, about 9 are pricing.

**Discounts are condition plus effect, evaluated on what is actually awarded.**
`evaluate_scenario` takes an allocation `{line: {vendor: pieces}}` and applies each vendor's stored `tier_rules`: quantity breaks first (at
the allocated pieces of that line), then slabs and thresholds on the post-break order value. It reports every adjustment with whether it
applied, the value it moved, and the value NOT captured (a missed discount, or a slab penalty from under-awarding). Lines that are
`not_comparable`, unpriced or absent are excluded and listed, never estimated. `tier_rules` stores a threshold but not its operator, so
discounts are read as strictly greater ("exceeding") and slabs as lower-bound inclusive (V1's sheet says so); this is a visible assumption.
Checked on real rows: V1 on the four 7-ply lines alone (Rs 96 lakh) falls into the +6 percent slab, Rs 5.8 lakh not captured; V2 on
everything earns 2 percent, Rs 7.95 lakh; V3's two quantity breaks sit on lines that are not comparable, so they never bite.

**Verification.** Against truth: 120 of 120 fields match on INR per piece and on comparability (`scripts/grade_normalization.py`);
12 offline tests in `tests/test_normalize.py`.

**Freight decision (made): landed cost is unknown by default, and nothing is ever defaulted.**
Every price row carries `freight_terms` (stated, included, extra, unknown) and `freight_status` (not_evaluated, estimated, stated, included), and starts
as `not_evaluated` with `landed_inr_per_piece` empty. There are now two flags: `comparability` is the ex-freight price flag (unchanged, still matches truth
on 120 of 120 rows) and `landed_comparability` is the one a landed-cost comparison must use; by default every priced row is `not_comparable` for
`freight_unknown`. A vendor-stated freight figure (recorded in `conditions` as value plus unit, percent of order value or INR per piece) is used as stated and
is not an assumption. Terms that include freight (DDP, or wording like "included" or "delivered") leave the price untouched. Terms that are "extra" with no
figure move to `comparable_with_assumptions` only when the buyer sets `freight_estimate_pct_of_order_value`, which is unset by default, editable, and carries
its 1 percent impact per row like FX and take-up. The estimate does not apply to vendors that are silent or contradictory (unknown stays unknown).
Freight wording is classified by keyword; anything the keywords do not settle is `unknown`, the conservative side. Limitations: no artifact contains a stated
freight figure, so the stated-figure path is covered by tests only and extraction does not yet parse one; all four extracted vendors say "extra".

---

## 10. Award engine (src/award.py)

**Five plain functions, one re-pricing step.** `single_vendor`, `cheapest_per_line`, `gated_split`, `max_vendors`, `max_share`. Each chooses vendors by
price on the chosen basis, then the allocation is re-priced once at allocated volume: quantity breaks, slabs and thresholds, MOQ overbuy, and freight on
the landed basis. Every result carries the naive and the re-priced total side by side, the adjustments, the value not captured, and what any saving is
measured against. `max_vendors` re-prices each candidate vendor subset once and keeps the cheapest; `max_share` moves whole lines only. Nothing iterates
to a tier-aware optimum, so a re-priced split can cost more than the baseline, and the result says so.

**Gate results are an input, not something the engine evaluates.** Questionnaire and attachment extraction is not built (and `claimed_unsupported` is
not yet on that side), so the caller supplies the vendors that passed. `gated_split` refuses without them. Any other strategy run without gates is
labelled "ignoring gates" with a blocking warning and is never recommendable. In grading, gate results come from truth (V1, V2, V5 pass).

**Savings are measured against the cheapest single vendor able to cover every line, re-priced, from the same vendor pool as the scenario.** Found by
a test: the first version drew the baseline from all vendors, so a gated scenario was being compared with V3, which fails the gates and has an expired
quote. If the allocation does not cover the same lines as the baseline, no saving is stated.

**Two bases.** `ex_freight` (default) shows totals labelled ex-freight with a warning naming the vendors whose landed cost is unknown. `landed` needs
`landed_comparability`; with freight unset it covers no line and says why. With a buyer estimate set, landed totals rise by the percentage and, because
one uniform percentage cannot change the ranking among vendors that all quote freight as "extra", the allocation and the saving do not change. Distance
does differ (V1 620 km, V2 250 km, V3 40 km, V5 120 km), so a per-vendor override is the obvious next lever; not built, since the decision was one
percentage.

**Feasibility and flags.** Eligible = priced and comparable on the chosen basis. MOQ policy is a parameter, default `overbuy` (the extra pieces are
priced at the vendor's net price and reported), or `exclude` (the vendor is infeasible on that line); the result states which. An expired quote
validity is a blocking warning, not an exclusion. A `needs_review` line is allowed but flagged, and blocks a recommendation above 5 percent of awarded
value. The share of value resting on assumption-derived prices is reported. A line no eligible vendor prices is listed as uncovered and never estimated.
A saving below 1 percent of the baseline gets an explicit "probably not worth the extra supplier relationships" (a stated product threshold).

**Section 3 now quotes the engine's live numbers, not the generator's design figures.** Section 3 used to say Rs 7.3 lakh naive to Rs 2.2 lakh re-priced (0.55 percent);
that came from the generator's outcome function, where the naive split assumed V2's discount earned. The engine defines naive as list price and re-prices from the
stored conditions, and its baseline (V2 as a single source) already earns V2's 2 percent and pays its MOQ overbuy. On the live store the gated split
(V1, V2, V5) saves Rs 11.9 lakh naively and Rs 1.0 lakh after re-pricing (0.26 percent of the baseline, 92 percent of the naive saving lost), using
three suppliers instead of one; `max_vendors` with two vendors (V2 and V5) saves Rs 43 thousand. The cheapest-per-line split ignoring gates saves Rs 13.1 lakh
naively and Rs 1.9 lakh (0.49 percent) re-priced.

**Verification.** Recomputed independently from truth prices, comparability, MOQs and the generator's tier constants: identical allocation, baseline
equal, re-priced total within Rs 1.71 of Rs 3.89 crore (rounding), best single vendor V2 as in the truth, and the gates remove the cheapest eligible vendor
on 7 lines (designed minimum 6; the earlier 25 counted V3's non-comparable lines). `scripts/grade_award.py`; 11 engine tests and 7 freight tests.

**Per-vendor freight overrides (V1 7 percent, V2 4 percent, V5 5 percent, the buyer's figures) change the split's verdict, not the vendor ranking.**
Stored as `freight_estimate_pct:<vendor>` assumptions, each with the value it applies to and its impact (+1 percentage point, and a 1 percent relative change;
V1 Rs 4.09 lakh and Rs 28.6 thousand, V2 Rs 3.98 lakh and Rs 15.9 thousand, V5 Rs 2.93 lakh and Rs 14.6 thousand). Precedence: stated figure, vendor override,
global estimate, nothing. Single-vendor ranking is unchanged (V2 Rs 4.06 crore landed, V1 Rs 4.37 crore; the gap widens from Rs 18 lakh to Rs 31 lakh). The gated
split flips from a Rs 1.0 lakh saving to a Rs 2.6 lakh loss against V2 as a single source (-0.65 percent): V1's 7 percent puts its four 7-ply lines further behind, and
four near-tie lines (3, 7, 14, 22) move from V5 to V2. With two vendors allowed, the best allocation on landed cost is V2 alone. V3 has no estimate, so its lines are
not landed-comparable. The overrides are freight percentages of order value, so they say nothing about how a vendor's distance interacts with truck fill.

---

## 11. Analyst layer (src/analyst.py)

**Three tools, one rule, one answer contract.** `run_sql` (one read-only SELECT against `procurement.db`; enforced by opening the file read-only, `query_only`, a
SQLite authorizer that allows only reads, and a single-statement check; 100 rows), `run_award` (the award engine), `explain_field` (the whole record: value, state,
reason, verbatim snippet and anchor, derivation, resolving question, and for prices the normalization chain). The system prompt is the live schema, a store
inventory (vendors extracted, row counts, freight state, whether gate results were supplied) and the rule up front: if the store cannot answer, say what is missing and
offer the drafted resolving question; never estimate what was not asked for. Numbers come only from tool results. Every answer is prose, an optional small table,
`refused`, `missing`, `drafted_question` and the tool calls as a collapsed expander (`Answer.expander`, `Answer.markdown()` renders a `<details>` block).

**The model cannot supply gate results.** `run_award` rejects a `gates` parameter; the model can only say `gated=true`, which uses the gate results the buyer supplied for
the session, and the tool output names their source ("supplied by the buyer, not evaluated from questionnaire documents"). With none supplied, a gated scenario is refused
with the drafted question. This matters because a model that could name the vendors that passed would be inventing the one input the store lacks.

**`baseline_vendor` measures a saving against a named single source.** If that vendor cannot cover every line (V5 misses the 7-ply lines) the tool says no such
baseline exists and states no saving.

**The analyst layer runs on either provider; the key that exists decides.** `Analyst` picks Anthropic (`claude-opus-5`) if `ANTHROPIC_API_KEY` is set, otherwise OpenAI (`gpt-5.5`) if
`OPENAI_API_KEY` is; `ANALYST_PROVIDER` overrides. Same three tools, prompt, rule and answer contract. Anthropic path: tools in `name`/`description`/`input_schema` form, `tool_use` and
`tool_result` blocks paired by id, and schema-forced output as a fourth tool, `submit_answer`, called last with `tool_choice` pinned to it (there is no JSON-schema response format
alongside free tool use, so pinning a tool is the forced-output mechanism). OpenAI path: the same tools as strict function tools, Responses API chaining, and a JSON-schema text format for
the answer. Only the analyst layer is provider-selectable; extraction stays on OpenAI.

**Refusal is a tested feature, but not yet proven on the model.** Offline, with the real `anthropic` SDK client over a mocked HTTP transport (so the SDK builds and parses the requests
and only the network is faked): the tool definitions as sent, tool results paired to their `tool_use` ids, the pinned final call, the 10-round limit, and the refusal rendering (missing item
plus drafted question); plus the read-only guard, the whole-record tool, gate handling and prompt ordering. Live tests (the kraft-paper question, and the gated question with no gates
supplied) are written and skipped unless `RUN_LIVE_ANALYST=1`. They have not run: the only key is `OPENAI_API_KEY` and that account has no credit (HTTP 429 `insufficient_quota`, re-checked with a one-word request), and there is no Anthropic key,
so the model's actual behaviour on the refusal and on the three canonical questions is unverified on either provider. Both loops are tested offline (the Anthropic one through the real SDK over a
mocked transport, the OpenAI one against a scripted Responses client). Add OpenAI credit (or an `ANTHROPIC_API_KEY`) and run `python scripts/run_analyst_questions.py all`; answers are saved to `dataset/eval/`.

**Found while building it:** `award.blocked_by` assumed any vendor reported as blocked was blocked by MOQ, so a vendor with no MOQ crashed the uncovered-lines report;
fixed with a regression test.

---

## 12. Questionnaire and gate extraction (src/extract/questionnaire.py)

**All 14 RFx questions are extracted, not 12: three mandatory gates (BRCGS packaging, in-house compression testing, FSC chain-of-custody) and eleven scored.** Scoring is not built.

**Two things are decided per answer, and they are separate.** `state` is the five-state model: extracted, needs_review (tried twice, still not sure), missing (no answer), claimed_unsupported.
`gate_status` (gate rows only) is the outcome: pass, fail, not_answered. `claimed_unsupported` lives on `questionnaire_answers.state` and never on `bid_fields`.
It means the vendor claims it holds something and no valid attachment supports it: none attached, the attachment expired on or before the evaluation date (valid through that date), or it was not
issued to the vendor. It is a determinate finding, not an extraction doubt: the gate fails, and the drafted question asks for the certificate.

**Same pattern as prices: the model interprets, code checks and decides.** The model returns each answer as a locator plus verbatim snippet, a stance (yes, no, partial, unclear, not_applicable) and every
certificate the answer mentions with its date exactly as written and whether it is an expiry. Code checks the words are at the locator, that the answer belongs to its question, and parses the date
(`30-Nov-2026`, `31/01/2027`, `28 February 2027`, `15 Dec 2026`; a partial date such as `Jan-2026` is never guessed). Failures go to a stronger second pass, then `needs_review`. Attachments (certificate PDFs)
are read the same way: kind, holder, issuer and valid-to date. Gate evaluation is deterministic: yes plus a valid attachment passes; yes with none, or an expired one, is claimed_unsupported and fails; no or "in progress"
fails; an unclear answer after two passes is `needs_review` and does not clear; no answer is `not_answered`. An attachment's expiry beats the vendor's own statement.

**Three inputs did not exist and were created from truth, each flagged.** `rfx.json` now carries the 14 questions and the evidence each requires (buyer-side). The attachments were never generated, so 10
certificate PDFs were rendered (`scripts/generate_attachments.py`; V4's, which names a different entity, is not rendered because V4 is not ingested). V5's response is an email with no questionnaire at all, so its
gates cannot come from a response; truth says its evidence is on the buyer's vendor master, so `dataset/artifacts/vendor_master.json` records that G1, G2 and G3 are on file. It invents no certificate numbers or expiry
dates: V5 clears on the buyer's own prior qualification, with `evidence_source = vendor_master` and no expiry, which the buyer must confirm is current. Truth was corrected to match: V5's 14 answers are `missing`.

**The alignment check has to be tight for PDFs.** A test swapped one answer for its neighbour's and the PDF check let it through, because the window took in the neighbouring question's text. It is now the answer line,
the line before it and the two after (in this layout an answer sits level with its question's first line). Certificate labels, numbers and dates are checked by containment in the answer's own text, not whole-cell equality.
The layout assumption is specific to this generator; a real vendor PDF with a different table shape may over-flag.

**Storage.** Additive columns on `questionnaire_answers` (state, gate_status, reason_code, stance, anchor, snippet, expiry_date, expiry_source, evidence_source, resolving_question) in `schema.sql`, and
`ensure_columns` migrates an existing database. `answer_text` is NOT NULL, so an unanswered question stores an empty string. In `truth.sqlite` the same columns hold the expected values.

**The analyst reads gates from the pipeline.** `Store` derives gates from `questionnaire_answers` (a vendor clears only with pass on all three mandatory gates). No gate rows means "not evaluated" and a gated scenario refuses;
gate rows with no passing vendor means an empty pool. An explicit `gates=` override remains for what-ifs and tests. The award engine and analyst prompt are unchanged.

**The two analyst-prompt lines that went stale were fixed afterwards.** The rules said gate results were "supplied by the buyer, not evaluated from documents", and the inventory line printed
"supplied by the buyer". Both now describe whatever `store.gates_source` says: the rules tell the model to report the source exactly as the tool gives it, and the inventory line prints
"<vendors> cleared all mandatory gates; source: <gates_source>", "no vendor cleared ..." when the questionnaire was evaluated and nobody passed, and "NOT available" only when it was not evaluated
and nothing was supplied. Tested for pipeline, override and empty-pool sources.

**Verification status.** Everything around the model is tested on the real artifacts with a perfect-reader stand-in: 56 answers, none wrong, V1, V2, V5 clear and V3 fails all three gates, 10 of 10 attachments right.
That says nothing about how the real model will read them: the OpenAI account has no credit, so the live extraction and its calibration have not run. The regression (`tests/test_gates.py`) shows gated_split refusing before the
questionnaire exists, and after it giving an identical result whether the gates are supplied by hand or read from the database; a live-database variant is skipped until the extraction has run.

---

## 13. Sanity report (SANITY.md, scripts/sanity_report.py)

**A read-only walk of the whole pipeline on the current database, regenerated on demand.** It covers extraction by vendor and state, normalization and assumptions, the questionnaire and gates, the award
scenarios (naive and true saving, vendors and lines), the calibration 2x2 across stages (live-model stages kept separate from the stand-in), and about 45 internal-consistency checks. It fixes
nothing; findings are flagged for a decision. On the current database it flags two things, both "not run yet": the questionnaire has not been extracted, and `questionnaire_answers` predates the
pipeline columns.

**The checks are themselves tested.** A test seeds a scratch copy with 30 known faults and requires the matching checks to fire; a clean copy must flag only the two expected items. That found one
bug in my own check (a comparison that is NULL, not true, when one side has no reason code, so a price flagged for arithmetic with an unflagged total would have passed) and a tautological MOQ
check (it asked whether the award engine blocked a vendor for a MOQ it does not have, which it cannot do), now a data check for a MOQ-like term not recorded as a MOQ with a number.



## 14. Warnings section and the editable review block threshold (src/award.py, src/settings.py)

**Warnings.** Every award scenario result now carries `needs_review_warnings`: each `needs_review` field on a line the scenario actually awards, with vendor, line, field, value, reason code, value at risk (Rs lakh), the resolving question, and `blocks_recommendation`. Sorted by value at risk, largest first. `summarize()` prints it as a Warnings section; the analyst's `run_award` result carries it too. It is not a refusal: below the threshold the scenario runs and the recommendation ships with its caveats visible. Above it the existing `needs_review_lines` block still applies.
- Value at risk is the awarded line's re-priced total, not the field's own contribution. Two doubted fields on one line (V2 line 20's price and total) each show the full line exposure; they are not additive.
- Only awarded lines are listed. A doubted field on a vendor or line the scenario does not use is not a caveat on that recommendation.

**Threshold is a setting, not an assumption.** `review_block_threshold_pct` (default 5) lives in a new `settings` table because normalization rebuilds `assumptions` and would silently reset it. `src/settings.py` validates (0-100) and is the single read/write path; `award.load()` reads it into `Context.review_share`. The `settings` table is created on first write; reading a database without it returns the default.

**Not done here.** The settings row on the Comparison and Event pages is UI, so it is left to Codex (brief below in the hand-off). The analyst prompt does not force the model to list the warnings; they reach it in the tool result, but a prompt line would be needed to require them in the prose. Figure on the live DB (gated split, stand-in gates V1/V2/V5): two Warnings entries, V2 line 20 unit_price and line_total, arith_mismatch, Rs 13.61 lakh at risk (3.5% of the award), under the 5% threshold.

**Settings row built (app/main.py, `render_settings_row`).** At the direct request of the user, in `app/`. Number input with the note as tooltip, above the award-note export and grid on Comparison and above the review queue on Event. Reads on the read-only connection; Save calls `settings.set_value` (its own writable connection), shows a `ValueError` inline, and reruns the page on success. It is the app's only write path, and it writes only the settings table; nothing is written on render (`test_all_pages_with_empty_schema` still asserts the DB bytes are unchanged).


## 15. Model metering, and V1's questionnaire sheet (src/metering.py, scripts/generate_vendor_docs.py)

**Metering.** Every model call (extraction, questionnaire, analyst on both providers, V4 vision) is recorded to `model_calls` (provider, model, stage, effort, input/output/cached tokens, elapsed). Recording is off until an entry point calls `metering.configure(db)`, so tests never write to the real database. `scripts/cost_report.py` prices the calls from a dated PRICES snapshot (2026-09-20). One full run cost about $1.41 (Rs 125), about $0.047 per line item; the analyst is half of it.

**V1's artifact.** V1's questionnaire had been rendered on the Terms & Conditions sheet (rows 19-32). It is now its own fourth sheet, "Questionnaire" (Ref, Question, Our response, Attachment; the attachment column names the certificate file V1 sent for questions 1-5), and T&C no longer carries it. Truth is unchanged (hash-checked). The artifact was not the cause of V1's failed gates: the model returned the bare sheet name with no cell for certificate cells, because the locator instruction said "a sheet name only where the dump shows one". Fixed in `questionnaire.LOC` (workbook locators are Sheet!Cell, never the sheet alone). With both changes V1, V2 and V5 clear all three gates and V3 fails all three; certificate dates parse 10 of 10. Four non-gate answers remain silently wrong (stance `partial` where truth is `yes`, on V1 q4, V2 q4/q5, V3 q3).


## 16. V4 persisted as the fifth vendor (src/extract/ratecard.py)

V4's photographed rate card is persisted from the two independent vision passes already on disk (dataset/eval/v4_vision_pass1/2.json); no truth is read. Per row, code merges the passes and keeps a number only when it can stand behind it: extracted needs both passes to read the same rate and unit with high confidence and the row to match one RFx line on dimensions and ply (Rfx.match); otherwise needs_review, with the value kept only if both passes read the same number (lines 22 and 27) and NULL if unreadable or disputed (lines 11-14, 23). Result: 30 rows, 23 extracted and 7 needs_review (a user brief said "26 clean"; the card has 30 lines). Prices stay USD (USD/piece for 20 rows, USD/kg for 10); normalization converts at the stamped 88.50. The header (vendor, date 07 March 2026, EXW Navi Mumbai) is not in the passes, so two further vision reads take it and the run stops if they disagree. New reason codes ocr_unreadable and ocr_ambiguous_digit with resolving-question templates. Anchor is ratecard:r<row>:c4 (truth's convention; the file is submissions.file_name). Footer conditions: MOQ 5,000 per size, validity 14 days, freight extra, 30% advance, FX risk on buyer. Only unit_price and declared_liner_gsm are written (the card has no totals).

Side effect fixed: questionnaire.run iterated every submission and crashed on a .jpg; it now skips submissions with no questionnaire source. Scenario figures did not change: V4 is never the cheapest on any line and does not clear the gates.


## 17. Demo polish (display only): Indian formatting, Processing panel, navigation, Ask chips (app/fmt.py, app/main.py)

Display-only. `app/fmt.py` is the one formatting helper: Indian digit grouping, INR prices 2 decimals, USD prices 3, totals and value at risk in whole rupees, percentages 1 decimal with a true minus, and a saving phrase without a double negative. Value at risk is now shown in rupees (not lakh) so the 0-decimal rule and Indian grouping apply. Processing panel on Event reads model_calls read-only: excludes `analyst:streamlit`, takes the latest run of the canonical question set, prices via `scripts/cost_report.py` (nothing restated in the app). Navigation is numbered 1. RFx, 2. Event, 3. Comparison, 4. Ask; the landing page stays Event. Ask has four question chips that fill the input. Left alone on purpose: model prose (raw by rule), and the award-note document's own money and percentage formats (tests pin them); only its saving line uses the new phrase.


## 18. Clarification wording and the per-vendor email (app/clarify.py, app/main.py)

Display layer only; stored resolving questions and derivations are unchanged. Vendor-facing questions for ocr_unreadable, ocr_ambiguous_digit, arith_mismatch, reference_unresolved, spec_incomplete and no_attachment are rebuilt at display time from templates with no internal terms (no passes, OCR, confidence or reason codes); other reason codes keep their stored wording. The arithmetic-mismatch template shows the product from the stored rate and RFx quantity (per-100 rates divided by 100). The source-evidence derivation is rewritten in plain English for the buyer. Comparison has a Clarifications section: pick a vendor, draft one email from every needs_review and missing bid_fields row (a price and its total that raise the same question are asked once), with Copy to clipboard and Download .txt. The Processing panel's analyst row is now labelled "Analyst (scripted question set)".

**Clarification email, questionnaire items added.** The per-vendor email now has two labelled sections, "Bid clarifications" (largest quoted-price exposure first, unknown exposure last, then by line) and "Questionnaire clarifications" (mandatory gates first, then scored questions), read from questionnaire_answers in state needs_review or claimed_unsupported. An empty section drops its heading; numbering runs across both. Questionnaire items use the display-time templates where one exists (no_attachment) and the stored wording otherwise (e.g. certificate_expired). Read-only aggregation.

**Email wording fixes (display-time, app/clarify.py).** line_absent and rate_blank share one vendor-facing template ("your quotation did not include a price for line X ..."). A stored question that quotes a truncated RFx question text is shown with the questionnaire's own wording instead (a display fallback; nothing stored is edited). The email intro now covers both sections.


## 19. Evals page (app/main.py, scripts/snapshot_evals.py)

Sidebar page "5. Evals": read-only display of the graders' recorded output. `scripts/snapshot_evals.py` runs the existing graders (grade_extraction, grade_questionnaire), copies the V4 vision calibration (dataset/eval/v4_vision_report.json), records the award engine's five scenarios, parses SANITY.md's flag count and runs the test suite once, writing dataset/eval/eval_snapshot.json. The app only reads that file and shows a stale notice if procurement.db is newer. Sourcing notes: there is no v4_ratecard_result.json (the V4 calibration is v4_vision_report.json); grader output was not previously saved anywhere, hence the snapshot. Captions are data-driven so they cannot claim zero silent failures when the data says otherwise.


## 20. Comparison page: selectors moved, live award preview (app/main.py)

The Vendor and Line selectors now sit directly under the "Source evidence" heading, above its card; the top of the page holds only the review-threshold setting and the award-note export. The selectors keep their keys, so cell clicks still drive them. The export section shows a live preview of the engine's own result for the chosen scenario and basis (recommended vendors or refused/infeasible, naive and true saving against the baseline, the engine's materiality sentence as the annotation, and its block-severity warnings), computed the same way the export computes it. Read-only; nothing new is decided in the app.


## 21. Plain-language award preview and export (app/award_words.py)

The Comparison preview and the exported award note now read the award engine's output in plain words, with registered vendor names (code only in brackets in tables, never in prose or the note). Every figure is the engine's own; the wording is chosen in `app/award_words.py`. The header rule follows the engine's own signal: a split whose `saving_after_repricing` warning is at warn severity is "Split not recommended. Awarding to a single vendor (X) saves more overall. See the trade-off below.", one at info severity is "System recommends this split"; a scenario with block warnings is "DO NOT USE THIS OPTION". The "why the difference" text is built from the engine's applied price-rule adjustments (for the live data the cost comes from Sahyadri's small-order slab, not from Kaveri's discount, which the split still earns). The single-source table runs the engine's single_vendor for each vendor. The scenario dropdown now offers all five engine scenarios (single source, split by line, cheapest per line ignoring gates, dual source = at most 2 vendors, bounded split = no vendor above 70%); the export accepts the same five. The exported note drops the engine's own "saving_after_repricing" line (the summary says the same in plain words) and uses plain labels for assumptions.


## 22. Kaveri's discount counted twice: fixed in procurement.db, truth left alone

Truth has ONE V2 order-value discount (rule 5, -2% above Rs 50 lakh, the page-2 footnote). procurement.db had TWO: the extractor read the same discount from the footnote (p2:L4) and again from V2's answer to questionnaire Q13 (p2:L50, "30 days net; 2% settlement discount on orders above Rs 50 lakh") and stored both, so the award engine applied -4%. Fix: deleted rule 6 (the Q13 restatement) from procurement.db (backup: dataset/eval/prior/procurement.before_v2_dedupe.db); truth.sqlite was not touched. Effect (ex-freight): Kaveri single source Rs 3.82 cr -> 3.90 cr; discount earned Rs 15.91 lakh -> 7.95 lakh; gated split true saving -Rs 4.81 lakh (-1.26%) -> +Rs 1.01 lakh (+0.26%), naive Rs 11.9 lakh unchanged, still below the 1% threshold, so still "do not split". This restores the figures in section 3. Not fixed (needs your decision): the extractor still stores an identical discount twice if a document states it twice, so a fresh extraction of V2 will reintroduce it; and grade_extraction compares tier rules as a set, so it cannot see a duplicate row.

**Event page total now comes from review_exposure (app/main.py).** The Review queue's headline total was a raw sum of per-field quoted-price proxies (overlapping fields on the same vendor-line, per-kg and USD rates not normalized). It is now the analyst layer's own `Store.review_exposure()["known_exposure_inr"]`: normalized INR per piece x annual RFx quantity, one exposure per vendor-line, ex-freight (label: "Total ₹ affected (normalized, per vendor-line, ex-freight)"). The per-row figures in the table are unchanged quoted-price estimates and are captioned as such, so the total is not the sum of the rows. A regression test compares the Event page's number to `review_exposure()` for the current database.


## 23. Tool results are formatted at the boundary (src/present.py, src/analyst.py)

The analyst tools still compute in full precision; `Analyst._dispatch` now passes every tool result through `present.for_model()` before the model sees it, so the model can no longer quote a raw float (it had printed Rs 18,712,301.1404928). Conventions: money totals are whole-rupee strings with Indian grouping ("₹1,87,12,301", negative "−₹4,80,827"); rupee rates are 2-decimal strings ("₹6.73"); a vendor's own quoted figure is kept as a string to 3 decimals ("0.076", so USD rates are never rounded to 2); percentages and shares are strings with at most 2 decimals ("0.26%", "26.17%"); any other float over 2 decimals is rounded to 2. Direct Store calls (and their tests) still return full-precision numbers. The system prompt now tells the model that tool figures are pre-formatted and to quote money as ₹X,XX,XXX with at most 2 decimals and percentages with at most 2. Tests (tests/test_present.py) walk every tool's JSON on the current database and fail on any numeric field with more than 2 decimal digits.


## 24. Event page restructured (app/main.py)

Layout and framing only; the queries and the review_exposure() call are unchanged. Order: five tiles (RFx lines, Vendors submitted, Fields extracted, Fields flagged, Total ₹ affected with the ex-freight label from the previous change kept), Vendor responses, Review queue (with the review-threshold widget inside a "Settings" expander plus a caption saying what it controls), and "How this event was processed" (a bordered callout with a one-line summary and the per-stage table in an expander, numbers read from model_calls). The queue's question column is now "Clarifying question": the first ~60 characters and a ▸ that opens the full drafted question in place. "Fields extracted" is one added count query over the latest submissions' bid_fields.


## 25. Comparison page restructured (app/main.py)

Layout and framing only; no query, normalization or award-engine logic changed. Order: four stat tiles (Total lines; All-vendor clean — every vendor's unit_price is extracted; Any flag — at least one vendor's unit_price is needs_review/missing/not_quoted; Cheapest overall — the vendor with the lowest normalized ex-freight total summed over the lines it has a normalized price for, a raw stat, not an award recommendation), the existing Award recommendation/preview (the review-threshold widget removed from here — it now lives only on Event's Settings expander), a view toggle (Normalized ₹/piece ex-freight, default; or Quoted rates), a legend caption, a "Jump to line" control, then the grid, then a single "Details" section (`st.container(border=True)`) with vendor/line selectors above a two-column split: Source evidence (left) and Draft clarification with a Copy-to-clipboard button (right).

Grid: new `comparison_html(..., mode, norm_by_pair)` renders a distinct `cmp-grid` stylesheet (the shared `CSS`/`quote-grid`/`STATES` used by the RFx questionnaire table is untouched), one line per cell (`COMPARISON_GLYPHS`: ✓/⚠/✗/–), the unit/basis or comparability note moved into the cell's `title` tooltip, a sticky header (CSS `position:sticky` inside a scrollable container) and a 4px left-border strip on the row (amber if any vendor is needs_review, else red if any is missing).

**Feature removed, not just moved:** the per-vendor bulk clarification email (vendor selector, "Draft clarifications" button, the multi-item email with Copy/Download) is no longer called from this page — the task's instruction to replace "Source evidence and Clarifications... with a single Details section" superseded it. `render_clarifications` still exists in the codebase (only the Event-page email feature built earlier is gone from Comparison) but is currently unreferenced and untested; say if it should come back.


## 26. Comparison grid: dark-theme contrast fix; click-to-select confirmed intact

Regression report after section 25: the grid looked unreadable and "unclickable" on a dark Streamlit theme. Root cause was mine, not a Streamlit theming gap: compacting the grid dropped the per-state cell background while keeping the same dark foreground colours (meant to pair with a light background), and the header/row-label cells had an explicit light background with no explicit text colour. Both are dark-on-dark (or light-on-light) in a dark theme.

Fix: `COMPARE_CSS` now pairs an explicit background with an explicit foreground everywhere in the grid (headers, row labels, and each state's cell, restoring the light-tinted-pill look the original quote-grid used, now on the compact one-line cell), so the table is a self-contained light card independent of the surrounding Streamlit theme, in both light and dark mode.

**Click-to-select was not actually broken.** It uses the same `?bid_vendor=&bid_line=#source-evidence` link/query-param mechanism as every other grid in this app (`apply_cell_link()`); a live click was tested in both themes and correctly updates the Vendor/Line selects and the Details pane. I did not move to `st.dataframe`/`st.data_editor`/AgGrid — that would have discarded the state icons, tooltips and row-border flags built for this page, which the task said not to touch, and testing showed the real bug was contrast, not interactivity. Say if you still want a native cell-selection widget instead of the link-based one.


## 27. Source-evidence popup, consolidated Vendor clarifications, ex-freight-only award preview (app/main.py, app/clarify.py)

**Part A — cell click.** `st.popover` cannot be triggered from inside a plain HTML table cell (it must be an actual Streamlit widget in the layout tree); the grid is one HTML blob per the section-25/26 redesign, so a click can't open one. Used `st.dialog` instead, opened immediately on the same click-navigation the grid already used (no more scroll-to-a-section-below): a card with vendor+line title, state badge, derivation, file/anchor, the verbatim snippet in a monospace block, and a Copy-to-clipboard button. It is gated by a one-shot `_open_evidence_dialog` flag set in `apply_cell_link()` so it opens only on a genuinely new click, not on every rerun (view-mode toggle, vendor dropdown, etc.).

The old two-column "Details" pane (Vendor+Line selects, Source evidence, Draft clarification) and the old bulk-clarification-email flow are both replaced by one new section, `render_vendor_clarifications()`, headed "Vendor clarifications": a single "Select vendor to review" dropdown (shares the `bid_vendor` session key with the grid's click links, so a click auto-selects the vendor here), a numbered card per open item (bid_fields needs_review/missing/not_quoted, or questionnaire_answers needs_review/claimed_unsupported) with plain-business-language wording, and "Copy full email"/"Download as .txt" for the whole list as one email, signed with a buyer-name text input. A clicked line's matching item(s) get a `.clar-pulse` CSS animation and a scroll-into-view. Per-item wording reuses `clarify.vendor_question`'s existing templates (already jargon-free) with the redundant leading "{Vendor}: " stripped (`plain_paragraph()`), since the section and the email are already addressed to that vendor by name.

Caught while writing the required "no jargon in the generated email" test: `open_items_for_vendor`'s questionnaire query omitted `reason_code`, so `clarify.vendor_question` silently fell through to the raw stored text for every questionnaire item (still not jargon, but not the intended template) — fixed.

Bug found and fixed while polishing: once the grid stopped injecting the shared `CSS` constant (section 25), the evidence dialog's `.evidence-badges` pills had no styling on this page. Injecting the shared `CSS` block as its own `st.markdown` call inside the dialog fixed the styling but silently emptied the derivation/snippet `st.code` blocks that render right after it (a `st.dialog` fragment quirk, not investigated further); fixed by inlining a small scoped `<style>` into the SAME markdown call that already renders the badges, adding no new element to the dialog.

**Part B — award note.** The "Award cost basis" selector is gone; the page always computes ex-freight (`cost_basis = "ex_freight"` at the call site only — `src/award.py`'s landed-basis computation and `generate_award_note`'s own `landed` default are untouched, so nothing is deleted from the engine, just not exposed here), with a caption explaining why. "Award scenario" is `st.radio(horizontal=True)` with the exact two labels asked for, "Single vendor" / "Split across vendors" (`SCENARIO_LABELS` renamed from the previous turn's wording).


## 28. Comparison and award recommendation polish

Comparison now always displays normalized ₹/piece ex-freight rates; the View and Jump to line controls are removed. Original quoted evidence remains in the cell-click dialog. The cheapest raw-sum vendor tile displays an eligibility warning when recorded mandatory gates fail.

Award recommendation replaces the former export heading. Its freight caption appears once above the scenario picker. The split preview promotes engine-derived naive and repriced savings into a two-column bordered callout with explicit foreground/background pairs for both themes. A below-threshold split is an amber advisory, with the existing explanatory sections retained. No prices, savings, gates or pipeline calculations were changed.
