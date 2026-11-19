# Model artefacts and scope

The microfluidic Extra Trees model artefacts are created locally after importing original, unit-consistent training data and completing grouped cross-validation. They are not bundled pre-trained. Keep artefacts with their data provenance and model cards.

The AI design & lab loop uses `design_engine.py` and `design_knowledge.py`: an expert-rule carrier ranker, literature-calibrated provisional priors, Gaussian-process residual corrections fit only on measured rows for the same drug/carrier, and Bayesian batch optimisation. The GP correction is not pre-trained; it is built at recommendation time from the SQLite `design_lab_runs` table. A response endpoint is GP-trained only when at least three measured labels exist for that endpoint. Kernel amplitude, length scale and noise are fixed conservatively rather than tuned against very small datasets.

Do not claim prospective predictive performance until independent prospective experiments have been conducted. Leave-one-drug-out diagnostics are retrospective internal checks and may not demonstrate cross-drug transfer.
