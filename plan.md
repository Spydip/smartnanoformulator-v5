# SmartNanoFormulator 5.0 implementation plan

## Current checkpoint (2026-11-17)

- Phase 0 is complete: the uploaded v4 archive was backed up separately and restored/tested at 42/42. v5 targets Render Free, rebuilds its runtime DB from the seed on every start, uses SQLite DELETE journaling, warns that history is disposable, and exports analyses/lab/import history as JSON.
- Phase 1 is complete: bounded/fallback library limits, UTF-8 byte-safe credential comparison, JSON/same-origin POST checks, distribution ignores, v5 labels, and source-checked E09 study classification are covered by regression tests.
- Phase 2 is complete: `ingredient_store.py` creates an additive source-aware schema, migrates all 64 profiles and 832 legacy property slots, retains raw source rows, and labels all 1,087 batches as direct-linked, explicit-form-linked, or unresolved. Explicit salts are forms with no inherited parent properties; prodrugs/mixtures remain unresolved. Retrieval dates missing in v4 remain null.
- v5 regression suite: 56 passing tests; startup health returns v5.0.0. v4 remains untouched.

Next work begins with Phase 3 source adapters and is not released until the full user checklist and deployment authorization are satisfied.

## Approved scope and constraints
Implement the supplied 5.0 Full Work Order in the order specified. Hosting choice is **B: remain on Render Free**. Preserve the existing v4 source and release; perform work in a separate v5 tree. At startup, v5 rebuilds a disposable runtime database from a bundled seed, explicitly discloses that analysis history and lab imports are disposable, and provides export of history. Do not make a paid-storage assumption. Keep the 42-test suite green except for explicitly agreed changes. Do not add new ML predictions; retain the measured-data-only design loop and withhold models unless paper-held-out validation beats the class-median baseline.

## Implementation approach
Retain the prototype's Python standard-library HTTP server, SQLite schema/data, ReportLab PDF generation, and single-page HTML/JavaScript UI. Add focused modules for ingredient resolution, provenance-aware data/source adapters, and export/seed initialization rather than replacing the evidence engine. External data sources are optional: a bundled, dated, source-attributed seed supports search and analysis offline; network lookup is restricted to unresolved/weak local matches, bounded by timeouts, retries, cache and rate limits. No retrieved value is displayed without named provenance. Chemical forms and ambiguous substances require user choice; no inferred molecular weight for mixtures/extracts/polymers.

Migrate existing drug profiles and formulation links additively, preserving source text and all existing values. Resolve legacy batch names only through reviewed aliases; leave uncertain names explicitly unresolved. Keep direct formulation evidence, related/same-class evidence and other-ingredient organ context separate. Never convert ingredient presence into a claim of formulation evidence, and do not recommend carriers without supporting evidence.

Redesign the existing UI into five primary destinations—Ingredient Search, New Analysis, AI Research Assistant, Literature Database, Reports and History—with technical workflows under Advanced. Reuse the current navy/teal research-studio identity, while simplifying wording, progressive disclosure, mobile layout, keyboard access, and property/source presentation. Show a brief result first; expose batch/composition/reference details on demand.

Rebuild reports as a traceable 14-section ReportLab PDF with Unicode font support, contents/page numbering, source and observation-type labels for each value, pending-review status where extraction has not been verified, and no synthetic charts or extrapolated release curves. Add an optional server-side LLM only after result and report work; it explains retrieved records, never supplies facts, and has grounding, citation validation, usage/cost accounting, quotas, spending cap, off switch and non-AI fallback. Catalogue additions are source-resolved, deduplicated, pending human review, and counted separately from evidence growth. Numeric outcome parsing retains source text and confidence; no model is published unless it beats the study-held-out median baseline.

## Project structure
- `server.py`: HTTP routing, request security, startup/seed initialization, JSON APIs and export.
- `engine.py`, `design_engine.py`, `hybrid.py`: existing evidence, measured-lab feedback and withheld/validated ML workflows; preserve their established boundaries.
- `ingredient_resolver.py` and `sources/`: canonical ingredients, forms, aliases, identifiers, property observations, source adapters and cache.
- `report.py`: the full traceable PDF.
- `web/index.html`: progressively disclosed responsive UI, five primary areas and Advanced workflows.
- `data_src/` and bundled seed database/data: immutable supplied sources and reproducible import inputs.
- `tests/`: offline tests and recorded source responses; regression coverage for existing and new behavior.

## Visual direction
Reuse the existing navy/teal academic-tool identity and components, but remove research jargon from first-use screens. Principles: trustworthy provenance visible beside facts; progressive disclosure; clear separation of evidence types; accessible high-contrast, keyboard-operable controls; comfortable mobile reading. Keep concise plain-language labels and restrained interactions; technical tables and methods remain accessible in Literature/Advanced. Brand essence: a careful, source-traceable nanovesicle research assistant; precise, transparent, approachable.

## Ordered delivery phases
0. Protect v4, record passing baseline, choose and document Free-tier seed/export persistence model.
1. Fix bad limits, byte-safe credential comparisons, JSON/origin checks on POST, journal policy, archive/Docker cleanup, version label, and verify/correct E09 study type against its primary source.
2. Add normalized ingredient/form/alias/identifier/property/source/link schema and lossless migration; preserve salts and unresolved records.
3. Implement offline-recorded adapters for PubChem, RxNorm, DailyMed, openFDA, NIH DSLD, PubMed and Europe PMC; provenance-aware seed importer (300+ ingredients) only from verified/attributed records.
4. Add local-first suggest/resolve/fetch, spelling/synonym/brand matching, ambiguity selection and form autofill.
5. Match evidence on ingredient IDs; preserve direct/related/context/none; fix study types and explain matches.
6. Simplify primary navigation and guided form; move technical workflows to Advanced; add accessible terms/tooltips.
7. Replace long results with summary, direct/related counts, carrier evidence statuses, next steps and demand-loaded details.
8. Rebuild full Unicode 14-section PDF and verify traceability.
9. Add optional grounded LLM with recorded model, validated citations, usage/rate/budget controls, off switch and deterministic fallback.
10. Grow catalogue through resolver/import review queue; report source, category and verification counts separately from literature evidence.
11. Parse experimental endpoints into numeric observations while retaining original text; only release models that beat study-held-out median; add measured release points, otherwise withhold.
12. Run acceptance/security/backup-restore checks; package and document v5 for side-by-side deployment; do not switch off v4 without user-controlled deployment approval.
