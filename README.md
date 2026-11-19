# SmartNanoFormulator v5.0.3

> This is the separate v5 working copy. The v4 source and database backup remain unchanged.

**Hybrid nanovesicle formulation research studio** combining the supplied literature/evidence catalogue, an evidence-retrieval assistant, transparent published-precedent matching, microfluidic process ML, and an integrated Bayesian formulation-design / laboratory-feedback loop.

## Start locally on Windows

1. Extract the ZIP into a normal writable folder (not inside the ZIP preview).
2. Install Python 3.10 or newer and select **Add Python to PATH** during setup.
3. Double-click `run_smartnano_v5.bat`.
4. On first launch the script creates a private `.venv` and installs the packages in `requirements.txt`. It then opens <http://127.0.0.1:8005>.
5. Keep the server window open while using the app. Close that window to stop it.

The v5 launcher uses port 8005 so it can run alongside legacy builds on ports 8000 and 8003.


## v5.0.3 user-friendly formulation designer

- Drug and supplement type-ahead search supports local canonical names, ingredient aliases and curated brand aliases (e.g. Panadol / paracetamol / acetaminophen / APAP). Brand names are search aliases only; users must check the exact package variant and active ingredients.
- Selecting a known ingredient auto-fills available molecular weight, LogP, pKa, aqueous solubility, TPSA, HBD, HBA and stability details. Numeric values parsed from source text retain the original source text in the property provenance view.
- Organ and tissue inputs now accept custom text, with an expanded suggestion list. Lack of a local organ record does not block analysis.
- When direct drug/target/carrier evidence is not retrieved, the app returns several carrier-specific exploratory candidates instead of stopping at “no data”. The RAG-style retrieval layer supplies relevant local evidence and carrier knowledge to the optional LLM synthesis. Candidates are explicitly separated from published formulations and measured results.
- Candidates are ordered by a transparent carrier-rule screening score based on route, target site, drug properties and release objective. The first item is a suggested starting point for investigation, not a performance or efficacy winner.
- The design engine can provide prior-only numerical batch proposals when required inputs and a supported design route are available. Those proposals have broad uncertainty and are hypotheses, not validated recipes. When key properties or route mapping are missing, the system still returns qualitative design concepts and variables to screen rather than inventing precise numbers.

RAG-style retrieval over the supplied literature, formulation records and carrier knowledge runs on the app server. For free cloud generative AI, configure the OpenRouter `openrouter/free` router using a server-side API key. No local model download is required. Free model availability and provider rate limits can change; prompts and retrieved excerpts are sent to the external model provider. The current first-release retriever uses structured and lexical matching; vector-embedding semantic search is a future enhancement, not a feature claimed by this build.

## What's included in v5.0.3

### 0. Identity-linked resolver, results and traceable reports

- Ingredient search is local-first and source-attributed. In the formulation form, curated brand/synonym search aliases (e.g. Panadol, paracetamol and acetaminophen) resolve to a canonical ingredient suggestion and auto-fill available properties. Brand aliases are not exact product identification: combination products and package variants must be checked. Ingredient identities, explicit forms, brand/product records and source-listed aliases remain separate; identity-linked properties include their source and review status. Ambiguous forms require user selection, and no parent molecular weight is inherited for a selected chemical form.
- The catalogue snapshot reports category totals and human-verified share. The packaged seed currently contains 358 ingredient records and 0% human-verified share; source-retrieved and legacy-migrated entries remain pending review. It also contains 68 product records and currently 0 explicit product-to-ingredient links, so a brand name must not be treated as an exact product or package identity. Curated search aliases suggest a canonical active ingredient but do not establish a product-to-ingredient link.
- Results use a summary-first view and keep direct ingredient/form matches, related same-class records and other-ingredient context separate. Evidence counts do not rank carriers or make compatibility recommendations; details are demand-loaded.
- PDF export generates a 14-section source-traceable report with embedded Unicode fonts, source-row identifiers, manual-versus-retrieved property attribution and a citation-record crosswalk. Missing release time-series are stated explicitly; narrative release text is not converted into numeric observations.
- History can be exported as JSON. On the included Render Free setup, the database is rebuilt from the bundled seed at startup; the export is an offline archive, not an automatic restore.

### 1. Literature and evidence features

- **Published-precedent matcher:** ranks real formulation batches using transparent field matches on drug, target organ, tissue, route and vesicle class. Its score is a heuristic relevance score, not an ML probability, evidence-quality score or efficacy prediction.
- **Local evidence retrieval:** searches the supplied formulation catalogue, nanovesicle knowledge base, organ-targeting examples and references, returning source record identifiers and matched terms.
- **Free cloud AI via OpenRouter:** uses the `openrouter/free` router from the server, with a server-side API key. The backend accepts only free model identifiers and returns citation-validated RAG responses or clearly labelled no-evidence hypotheses. Provider free limits and availability can change.

### 2. AI design & laboratory feedback loop

The new **AI design & lab loop** combines the core design engine from the separate NanoVesicle-AI prototype with the SmartNano evidence database.

- **Carrier ranking:** transparent expert-rule scoring across liposomes, niosomes, transfersomes, ethosomes and phytosomes. This is not a trained classification model.
- **Design space:** generates formulation-variable ranges for the selected carrier and release profile.
- **Surrogate model:** literature-informed prior plus Gaussian-process residual correction. The production design loop fits the GP only on measured rows for the **same drug and carrier**; cross-drug transfer is not assumed. Kernel scale, amplitude and observation noise are fixed at conservative prior-scaled values to avoid unstable hyperparameter tuning on very small lab datasets.
- **Bayesian batch optimisation:** proposes a diverse batch set using expected desirability plus an exploration bonus. Predictive intervals are shown; the optimiser can explore uncertain regions rather than selecting only the current highest score.
- **Bench recipe and method suggestions:** supplies a starting recipe/plan for review. It is not a validated SOP.
- **Persistent laboratory feedback:** user-entered measured size, PDI, zeta and/or EE values are stored in a new SQLite `design_lab_runs` table. New runs influence the GP correction on the next recommendation. Simulated/virtual outputs are not accepted by the saving workflow.
- **Validation status:** a leave-one-drug-out retrospective diagnostic is displayed only after enough measured rows exist (at least 20 rows across at least 5 distinct drugs). This is not prospective validation.

**Important interpretation:** until you save sufficient real data for the selected drug/carrier, the design loop is prior-only. The GP fits an output only after at least three measured labels exist for that output. Untrained outputs continue to use wide prior uncertainty. Predicted size, PDI, zeta and EE values are experimental hypotheses, not guaranteed properties.

#### Prior calibration and limitations

Some starting size and PDI anchors were adjusted using the SmartNano literature catalogue's clearly labelled, parseable outcome text. The extraction deliberately excludes radii, ambiguous measurement modalities, unlabelled numbers and PDI percentages that cannot safely be treated as fractions. Approximate size anchors (nm) are Liposome 128, Niosome 216, Transfersome 190 and Ethosome 207. Phytosome remains at the expert prior (250 nm) because the parsed sample is sparse. PDI anchors are 0.21, 0.26, 0.27, 0.29 and 0.30 respectively; the phytosome anchor is retained as expert-informed due to sparse evidence.

Uncertainty was widened to reflect the observed between-paper spread (size log-SD 0.50, PDI SD 0.14, zeta SD 20 mV, EE SD 30 percentage points). The zeta and EE point priors were not recalibrated because the catalogue text has inconsistent signs/measurement descriptions and too few consistent records for a defensible class-level fit. Most formulation slopes, carrier rankings, routes, specifications and recipes remain expert rules and must be reviewed by a formulation scientist. These catalogue summaries are not a clean supervised training matrix.

The release profile influences the search-space suggestion, but **the app does not predict release kinetics** or a target such as 65% release at 24 h. A release goal is stored as a design objective only.

### 3. Exploratory ML for microfluidic liposome process data

- CSV/XLSX/XLSM importer with duplicate fingerprints, provenance logs and data-quality checks.
- Extra Trees regression candidates for **particle size (nm)** and **PDI (fraction)** only.
- Grouped cross-validation by `study_id`/DOI where there are at least two groups; otherwise it falls back to `lab_id`, then `operator_id`.
- Comparison against a fold-matched median baseline. A candidate is exposed to the predictor only if it beats that baseline in grouped CV.
- Separate independent-holdout import. Holdout rows are never used for fitting or cross-validation; in-domain holdout MAE is reported separately.
- Synthetic/augmented rows are excluded from training. New imported data invalidate stale trained model files.
- Applicability-domain checks abstain when required inputs are missing, units differ from those used during training, or values fall outside observed training ranges/categories.

**Model scope:** the microfluidic workbench estimates process responses for microfluidic liposome data. It does not predict encapsulation efficiency, zeta potential, release profile, drug loading, organ targeting, toxicity, clinical efficacy or optimized formulations. It must not be generalized to other vesicle types without separate data and validation.

### 4. Preserved research workflows

The original catalogue, organ/tissue explorer, knowledge base, modification matrix, references, formulation analysis, analysis history, worked example and PDF report are retained.

## Critical data and benchmark status

The supplied project materials report a 928-run benchmark and the UI labels its metrics as **abstract-reported, not reproduced**. The 928 raw source rows and original fitted models/preprocessing artifacts were not present in the uploaded project archive. These reported values are context only: they are not loaded into training, are not presented as independently reproduced results, and are never used as predictions.

A separate public microfluidic liposome dataset is listed at Zenodo, DOI `10.5281/zenodo.17867478`. Its record describes 304 cleaned formulations plus a separate 12-row independent wet-lab validation file. This is a different dataset, not the missing 928-run source. It is not bundled or imported automatically.

## Public Zenodo dataset: recommended import sequence

1. Obtain the official source files from the Zenodo record and read its data dictionary.
2. Import `initial_formulations_raw.xlsx` as one training upload. If every row truly came from one source, set upload-level group type **Laboratory** and label `Lab_A` based on the source documentation.
3. Import `new_formulations_raw.xlsx` as a separate training upload with its documented source label `Lab_B`.
4. Import `wet_lab_validation.csv` separately and check **Mark this entire upload as independent holdout / wet-lab validation data**. Do not let these rows enter model fitting.
5. Retain original row IDs and verify columns, units, and outcome meanings before training.
6. Do not import Gaussian-noise or SMOTE/augmented extension files as original experimental measurements.

Use upload-level group labels only when every row in that upload truly came from the one named lab/operator. If a file combines multiple labs, retain row-level provenance; never assign one label to the whole merged file.

### Units are important

For the public Zenodo microfluidics dataset, its data article documents ESM/HSPC/CHOL/PEG concentrations in **mg/mL** and TFR in **mL/min**. The importer defaults to these units. It also supports explicitly labelled **mM** and **mL/h** datasets. It does not convert between them. All training uploads must use the same concentration unit and the same flow-rate unit; mixed-unit training is blocked. Historical v3.1 columns are migrated and retained as legacy `mM` / `mL/h`, not silently relabelled.

The accepted process inputs include ESM, HSPC, CHOL and PEG concentrations; total flow rate (TFR); flow-rate ratio (FRR); chip type; and aqueous medium. Outcomes are particle size in nm and PDI as a fraction from 0 to 1. Normalize percentage-style PDI only after checking the original definition. Ratio strings such as `5:1` are not converted automatically.

## How to use the AI design loop

1. Open **AI design & lab loop**.
2. Enter the drug name, MW, LogP, ionisation and aqueous solubility. Leave pKa blank if unknown/not applicable.
3. Choose route, target site, target organ, release profile and a carrier (or Auto).
4. Review the carrier score (expert rule), design bounds, predicted intervals, bench plan, recipe and retrieved literature precedents.
5. Prepare batches according to your approved laboratory procedures and measure the outputs using suitable validated methods.
6. Enter only actual measured values in the lab-results table and save the round. Leave unmeasured endpoints blank; at least one measured endpoint is required for each saved row.
7. Generate another proposal. When there are at least three same-drug/same-carrier labels for an endpoint, a GP residual correction can be fitted for that endpoint. Very small datasets do not trigger kernel hyperparameter optimisation; the covariance settings remain fixed and conservative.
8. Retain an independent prospective validation set for any scientific claim. A retrospective leave-one-drug-out result is only a diagnostic.

The recipe and formulation suggestions are starting hypotheses; no guarantee of vesicle formation, loading, stability, safety, organ targeting or therapeutic performance is made.

## Importing microfluidic process data

1. Open **Hybrid AI & ML studio**.
2. Use the in-app CSV template or a workbook with one experimental run per row.
3. Retain stable `run_id` and genuine `study_id`/DOI, `lab_id` or `operator_id` provenance.
4. Select the actual concentration and TFR units. Do not pool incompatible units, protocols, definitions or instruments without a justified harmonization procedure.
5. Mark synthetic/augmented rows using `is_synthetic`; they are excluded from fitting.
6. Import independent validation as a separate upload and mark it as holdout.
7. Train only after the readiness conditions below are met. Preserve an untouched test/validation source for final assessment.

Excel import reads the active worksheet. Formulas use cached values, so recalculate/save the workbook in Excel before upload. Upload maximum is 5 MB and 5,000 data rows. The importer rejects negative concentrations/flow values, zero TFR/FRR, non-positive sizes and PDI outside 0–1.

## ML training and interpretation policy

- At least 50 original non-synthetic, non-holdout rows and at least 2 independent groups are required for microfluidic model readiness; each endpoint needs at least 30 labelled rows and at least two groups.
- Grouped `GroupKFold` validation is used; `run_id` is never accepted as an independent group or model feature.
- The same fold split is used for Extra Trees and the median baseline. Only a candidate that beats the median baseline in grouped CV is exposed to the predictor.
- Only process/formulation fields are used as features. New imported rows invalidate model artifacts.
- Applicability checks use observed marginal feature ranges and categories. Being inside each individual range does not prove that a combination is supported or safe.

## Free Render hosting note

The approved v5 hosting target is Render Free, with no paid persistent disk. At every service start, `start.sh` replaces the runtime database with the bundled seed. Saved analyses, lab feedback, imported datasets and trained artifacts in the runtime filesystem are disposable and can be lost on restart or redeploy. The home ingredient-search page displays the reset warning and a direct JSON history-export link. The export is not an automatic restore. SQLite uses the default DELETE journal mode to avoid WAL sidecar surprises when the seed is copied. This is suitable for demos, not as the sole storage for laboratory data. See `DEPLOY_ON_RENDER.md`.

## Database, backup and hosting

The SQLite database contains the supplied literature/evidence catalogue plus user-imported microfluidic rows and logs. The AI design loop creates an additive `design_lab_runs` table in `smartnano.db`. Back up the database before replacing application files or deploying updates. Local trained microfluidic artifacts are in `ml_models/artifacts/` and should be retained only with the associated data provenance and model card.

The archive includes a Dockerfile and Render configuration. Hosted startup refuses to run unless `APP_ACCESS_PASSWORD` contains at least 16 characters. Use a unique random secret and keep it outside source control. This is a shared-password application, not an account-based multi-user system. Render Free does not provide persistent storage; do not assume the runtime database survives a restart.

## Tests

Run in the project folder:

```powershell
python -m unittest discover -s tests -v
```

The v4 42-test baseline remains preserved. The v5.0.3 cloud-AI working copy passes 117 unit tests, Python compilation and JavaScript syntax checks; local HTTP checks cover health, catalogue statistics, startup seed migration and hosted-password fail-closed behavior. The v5 package has not been deployed; Phase 11 numeric-literature parsing/paper-held-out model validation and a deployed backup/restore drill remain incomplete. See `TODO.md` for the acceptance status.

## Research-use statement

Research decision support only. Not clinically validated. No dosing advice. No guarantee of safety, efficacy, optimized composition or target-tissue delivery. Each formulation candidate, model result and extracted literature statement requires independent scientific review and experimental validation.


## Free cloud AI and Render deployment (recommended)

1. Follow `DEPLOY_ON_RENDER.md` to create the private GitHub repository and Render Blueprint.
2. Create an OpenRouter API key and add it to Render as `OPENROUTER_API_KEY`; set a strong `APP_ACCESS_PASSWORD` at least 16 characters long.
3. Keep the model set to `openrouter/free`. The server rejects model IDs that are not explicitly free (`:free` suffix) so this configuration cannot silently select a paid model.
4. For local Windows testing with cloud AI, copy `.env.example` to `.env`, paste your OpenRouter key into `OPENROUTER_API_KEY`, and keep `.env` private.
5. Launch `run_smartnano_v5.bat`; this package opens at `http://127.0.0.1:8005` after checking the health endpoint. It deliberately avoids the legacy `8000` port.

The OpenRouter free model tier currently publishes up to 50 requests/day for free variants. Limits and model availability can change. See `CLOUD_AI_SETUP.md` and `DEPLOY_ON_RENDER.md`.

## Launching v5

Use `run_smartnano_v5.bat` (or `run_smartnano.bat`, which forwards to it). The dedicated v5.0.3 address is port `8005`; this is deliberately different from the legacy `8000` address used by older builds.
