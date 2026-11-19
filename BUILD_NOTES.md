# SmartNanoFormulator v4.0.0 build notes

## Integrated changes

- Merged NanoVesicle-AI's portable carrier-ranking, prior/GP surrogate, Bayesian batch optimiser, recipe and formulation-plan logic into the database-backed SmartNano web app.
- Added the **AI design & lab loop** page plus `/api/design/config`, `/api/design/history`, `/api/design/recommend` and `/api/design/save-results` endpoints.
- Added additive SQLite `design_lab_runs` storage for actual measured lab rows. The lab loop refuses empty/invalid results and never accepts virtual-lab simulations as training measurements.
- Restricted the active GP correction to the same drug and carrier design space; cross-drug transfer is not assumed. Added leave-one-drug-out retrospective diagnostics only when at least 20 rows across 5 drugs exist.
- Added batch diversity with an exploration bonus and 95% uncertainty intervals. Size uncertainty is handled on log scale and displayed as multiplicative intervals. GP kernel settings are fixed to prior-scaled values to avoid unstable hyperparameter fitting with small laboratory datasets.
- Recalibrated provisional size and PDI prior anchors from explicitly labelled parseable literature outcomes. Widened uncertainty to reflect between-paper variation. Kept sparse/ambiguous zeta and EE point priors conservative.
- Clearly labels carrier ranking as expert rules, prior-only outputs as wide-uncertainty hypotheses, and the 24-hour release objective as user-entered only (no release-kinetics prediction).
- Preserved the published-precedent matcher and local evidence assistant; catalogue records are used as precedents, not silently converted to numeric GP labels.
- Retained the microfluidic Extra Trees workbench with source grouping, unit-aware import, independent holdout handling, grouped CV and baseline comparison.
- Updated Windows launch/version labels and documentation.

## Verification

```powershell
python -m py_compile server.py engine.py hybrid.py report.py design_engine.py design_knowledge.py
python -m unittest discover -s tests -v
```

Verified in this build: 42 tests pass; JavaScript syntax check passes; temporary-database HTTP smoke test passes for health, SPA, design config/history, recommendation, precedent retrieval, measured lab save and GP re-proposal.

## Data and model status

- The original 928-run raw microfluidic dataset and original fitted models remain absent. The abstract-reported metrics are not used.
- The external Zenodo dataset described as 304 cleaned rows plus 12 independent validation rows is not bundled or imported automatically.
- No pre-trained optimizer/GP model is shipped. It starts from transparent literature-informed priors and begins its correction only from measured laboratory rows saved by the user.
- The microfluidic workbench's Extra Trees model is separately trained only after valid user data import and grouped validation readiness.

## Scientific limitations

- Catalogue prior recalibration is an explicitly filtered descriptive summary, not a clean supervised dataset. It avoids radii and ambiguous units but still reflects publication heterogeneity.
- Carrier suitability and recipe suggestions are expert rules, not trained models or validated protocols.
- The Bayesian optimiser's scores depend on provisional prior assumptions and require prospective bench validation.
- The design loop predicts size, PDI, zeta and EE only; it does not model release kinetics. A target like 65% cumulative release at 24 h is stored as an objective, not predicted.
- The microfluidic predictor applies only to microfluidic liposome size and PDI under its imported data domain.
- Literature matching is heuristic retrieval, not a predictor or evidence-quality score.
- The assistant is retrieval-only by default. Generative synthesis requires user-configured API credentials and may incur costs.
