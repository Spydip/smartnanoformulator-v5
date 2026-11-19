# SmartNanoFormulator 5.0 outcome checklist

Items follow the approved work-order order. An item remains open until the stated behavior is delivered and evidenced. v4 remains untouched and deployed until v5 acceptance.

**Current status (2026-11-18):** Core Phases 0–9 are implemented; 104 v5 tests pass and the 42-test v4 baseline remains preserved. Phase 10 reporting and provenance are implemented, but full target coverage/human verification/product links remain open. Phase 11 literature numeric parsing and paper-held-out release-model validation remain incomplete. Phase 12 is documented and locally smoke-tested only; v5 has not been deployed and no Render service has been modified.

## Phase 0 — Protect v4 and document Free hosting (complete)
- [x] Separate v4 backup and v5 working tree exist; dependency-complete v4 baseline is recorded at 42 passing tests.
- [x] Verify a source+database backup restores into an empty folder and the app starts with all 42 tests passing (`/home/ubuntu/work/v4-restore-tests.txt`).
- [x] Record Render Free choice in README: seed database is rebuilt at startup; saved analyses/lab/imported data are disposable on restart; add user history export; state this limitation on home page. Do not claim persistence across Render restarts.

## Phase 1 — Quick repairs (complete; archive hygiene is enforced at packaging)
- [x] Invalid `/api/library?limit=abc` returns HTTP 400 or a safe default, never 500; include regression test (default 80; HTTP smoke test returns 200).
- [x] Basic-auth username/password comparison is byte-safe, including non-ASCII credentials.
- [x] Every POST requires `Content-Type: application/json` and a same-origin `Origin` or `Host`; cross-site and non-JSON submissions are rejected.
- [x] Explicitly chose SQLite default DELETE journal mode; seed database reports `delete`. Added `.dockerignore`; archive creation will exclude `__pycache__`.
- [x] Fixed obsolete “HYBRID AI · V4.0” label to “SMARTNANO · V5.0”.
- [x] Checked E09 primary paper: E09-F01–F06 are physicochemical characterization only; E09-F07/F08 are human volunteer irritation/tolerability only, not clinical efficacy. Source-linked correction has an offline regression test.

## Phase 2 — Ingredient schema and lossless migration (complete)
- [x] Add `ingredient`, `ingredient_form`, `ingredient_alias`, `ingredient_identifier`, `property_observation`, `product`, `product_ingredient`, `source_record`, and `formulation_ingredient_link` tables as defined in the work order (`ingredient_store.py`).
- [x] Migrate all 64 existing profiles with identical values and source text; all 832 legacy property slots are represented as observations with units/type/source/source ID/status where known; retrieval dates remain null where v4 had none.
- [x] Every one of 1,087 formulation batches has a direct-canonical or explicit-form link, or an explicit unresolved state. The 61 batch links for nine distinct explicit salt forms retain a separate `form_id` and inherit no molecular-weight observation. Abbreviations without exact canonical text and all ambiguous names remain pending/unresolved; no fuzzy link is used.
- [x] Keep the 42 v4 tests green unless an intentional behavior change is agreed and listed; the preserved v4 restore baseline remains 42/42, and the v5 suite now passes 56 tests.

## Phase 3 — Source adapters and seed import
- [x] Implement separate PubChem, RxNorm, DailyMed, openFDA, NIH DSLD, PubMed and Europe PMC adapters; each has bounded timeout, at most two back-off retries, local cache, polite rate limit and structured error result.
- [x] Adapter tests replay recorded responses without network access. Imported observations carry source/retrieval metadata, source IDs and applicable licence/attribution notes; missing legacy terms receive a cautious startup note.
- [x] Provide a reproducible categorized importer for more than 300 targets and report counts plus verified share; ambiguous forms/products are not guessed. Live lookup is opt-in after local lookup.

## Phase 4 — Local-first ingredient search and autofill
- [x] Normalize case/punctuation/accents while retaining salt words; match local exact aliases before local fuzzy candidates, then call external services only if local results are weak/empty.
- [x] Local suggestions distinguish canonical identity, synonym, no formulation evidence and not-found states; source-level failures remain visible.
- [x] Acceptance searches cover the acetaminophen/paracetamol spelling and synonym cases, product-vs-ingredient separation for Tylenol, and no-guess behavior for Panadol when no source listing is linked.
- [x] Magnesium glycinate/bisglycinate requires explicit form selection; unresolved multi-component identity has no single MW. Creatine resolves with identifiers and sourced MW.
- [x] Selecting a result fills the analysis form; properties carry value/unit/kind/source; ambiguous names/forms/products require user choice; missing values remain unavailable rather than model-filled.
- [x] `Madeupmycin` remains an ingredient-not-found state distinct from no formulation evidence; failed external services do not erase usable local data.

## Phase 5 — Evidence engine by ingredient ID
- [x] Query evidence by canonical ingredient/form IDs, not free-text drug strings; retain direct, related/same-class, other-ingredient context and none as distinct counts and messages.
- [x] Curcumin on skin returns its ethosome batch as direct evidence; resveratrol is “Ingredient found, no formulation evidence” (zero direct batches), not “not found”.
- [x] Study-type labels are not promoted beyond source support; every match has a plain-language rationale/update date. Carrier names are not recommendations without direct evidence.
- [x] Direct, related and organ-context counts remain separate; organ-level counts are not presented as drug-specific evidence.

## Phase 6 — Simpler website
- [x] Five primary areas: Ingredient Search, New Analysis, AI Research Assistant, Literature Database (formulations/organs/knowledge/modifications/references), Reports and History. Put design/lab loop, microfluidic workbench, methods/limitations and case study under Advanced.
- [x] Search is the default home focus. Guided form order is ingredient/properties, organ then dependent tissue, route, five editable pre-ticked carriers, run analysis. Organ and route are explicitly required; values distinguish user choices from source-filled data.
- [x] Scientific terms PDI, zeta, entrapment efficiency and loading have tooltips; Gaussian-process/database detail remains under Advanced. Initial mobile layouts were inspected at 390 px and the result summary at 360 px.
- [ ] Formal usability with three first-time users and a complete keyboard-only accessibility pass remain outstanding.

## Phase 7 — Simple results
- [x] First view shows a plain-language status, direct/related/context counts side-by-side, five carrier rows in fixed non-ranking order, and next-step navigation.
- [x] Batch data, compositions, study types, organ context and references are demand-loaded/expandable. A 360 × 844 px smoke render shows the summary, counts and five carrier rows above the fold.
- [x] “Explain these results” is available only through the explicit, controlled Phase 9 path; blank scientific values say “Not reported”, units are shown, catalogue scope/update date is stated, and not-found remains distinct from no evidence.

## Phase 8 — Full traceable PDF
- [x] Report includes all 14 work-order sections: cover; executive summary; ingredient profile; search/matching; evidence overview; carrier comparison; formulation batches; experimental results; biological evaluation; release kinetics; references; AI interpretation; limitations; appendix/glossary.
- [x] Cover/contents/page numbers, “Page X of Y”, repeating table headers, fixed table widths and units in headings are present. Bundled Unicode TrueType fonts preserve ±, µ, ζ, Greek symbols and subscripts without ASCII folding.
- [x] Properties distinguish source/manual/missing values and include source IDs, dates and extraction notes. Study-type labels are mapped only when supported; unknowns remain pending. Long source strings are explicitly excerpted while retaining source-record IDs.
- [x] Live reports for acetaminophen on skin and curcumin on skin contain all sections, Unicode and page labels; both state “No release time-series recorded.” The acetaminophen long-observation layout failure was fixed and regression-tested.
- [ ] Ten randomly selected PDF numbers each trace to a source row in two clicks or fewer.

## Phase 9 — Optional grounded LLM
- [x] API key is server-side; model allowlist/price list, input/output bounds, timeout, per-Basic-Auth-username/day cap, monthly cap and explicit `LLM_ENABLED=0` off switch are implemented.
- [x] Answers receive only backend-retrieved records/result JSON tagged with IDs; citation IDs and DOI strings are validated; source text is treated as untrusted data; clinical safety/efficacy claims and empty-retrieval answers are blocked.
- [x] Model/date are saved; usage audit records feature, model, tokens, price-list estimate, latency and status; quota/spend limits return clear statuses.
- [x] With LLM disabled, local search, analysis, summary and PDF remain functional; the template path identifies itself as non-AI; fake citation/DOI and missing-property cases are covered by tests.

## Phase 10 — Catalogue growth
- [ ] Full coverage of the specified 115-name starting drug scope is not verified. The current categorized seed/import has 358 ingredient records; 0% are human-verified and auto-retrieved/legacy records remain pending review.
- [x] The app reports counts by category and verified share; candidate duplicates are flagged, unresolved names are queued, and review statuses include pending/partial/verified/rejected states.
- [ ] Product records remain distinct and display active-ingredient text only as source label content, but the current seed has 68 product rows and 0 explicit product-to-ingredient links.
- [x] PubMed/Europe PMC records are literature candidates only; catalogue imports add zero formulation batches. Legacy PubChem/formulation records receive cautious source-term notes, and source records preserve licence/attribution metadata.

## Phase 11 — Numeric observations and evidence-gated ML
- [ ] Add numeric particle-size, PDI, zeta, entrapment observations retaining the original free text, paper and parse-confidence; review zeta signs and leave multi-analyte EE rows empty. Drug loading remains unparsed until data support it.
- [ ] Add release time-point records. Fit listed release models only to measured points and report fit statistics; never extend curves beyond the last time point.
- [ ] Reproduce paper-held-out validation against same-class median baseline (never batch split); publish no model unless it beats baseline. If it does not, show “withheld” and reason. Any prediction includes version, training count, baseline error, interval and in-range status. Keep measured-data design loop. Do not claim the unreproduced 928-run benchmark as project performance.

## Phase 12 — Release
- [ ] Full acceptance against deployed v5 and a backup/restore drill are not complete. Local checks cover health, category statistics, source-note backfill, history JSON export, PDF generation, and hosted-password fail-closed behavior; JSON history is export-only, not an automatic restore/import feature.
- [ ] No v5 deployment or v4 switch has been performed. Render Free seed rebuilding and history export are documented; retain v4 until remaining acceptance criteria are completed.
- [x] No user-controlled Render service was deployed or modified. Release instructions and a separate v5 package are prepared; external deployment still requires explicit authorization.
