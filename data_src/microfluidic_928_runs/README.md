# Microfluidic model data intake

This folder is a drop location for verified, original microfluidic liposome run data. No 928-run raw file or pre-trained estimator is included in this project archive. Do not create synthetic rows to meet the training threshold.

Use the in-app CSV template and retain one experimental run per row. Recognized inputs include ESM/HSPC/CHOL/PEG concentrations, total flow rate (TFR), flow-rate ratio (FRR), chip type and aqueous medium. Recognized outcomes are particle size in nm and PDI as a fraction from 0 to 1. The public Zenodo microfluidics dataset uses mg/mL for lipid concentrations and mL/min for TFR; other units must be explicitly selected and must not be pooled with differently labelled data.

Include stable run identifiers and genuine source provenance (`study_id`/DOI, `lab_id`, or `operator_id`). If a file contains rows from one known lab/operator but no such column, use the upload-level provenance field in the import UI. Import separate labs/operators as separate files. Never assign one provenance label to a file that combines multiple sources.

Import independent wet-lab validation data as a separate upload with the holdout option selected. Do not include Gaussian-noise or SMOTE/augmented records as original measurements. See the project README for dataset-specific handling and validation policy.
