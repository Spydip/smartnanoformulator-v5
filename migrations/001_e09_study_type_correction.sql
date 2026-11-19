-- E09 is DOI 10.3390/antiox13010091. The paper reports physicochemical
-- microfluidic/blank ethosome characterization and a separate single-application
-- human-volunteer irritation patch test of ET-CUR and ET-PIP (20 participants).
-- The latter is tolerability evidence, not clinical efficacy evidence.
-- Keep human-volunteer classification only on the drug-loaded batches that were
-- evaluated; clarify the scope in Evidence_Location for every row.
UPDATE formulations
SET Evidence_Level = 'Physicochemical only',
    Evidence_Location = 'Physicochemical size/dispersity characterization only for this batch; the paper’s separate human-volunteer patch test assessed ET-CUR/ET-PIP skin irritation, not therapeutic efficacy.'
WHERE Paper_ID = 'E09'
  AND Formulation_ID IN ('E09-F01','E09-F02','E09-F03','E09-F04','E09-F05','E09-F06');

UPDATE formulations
SET Evidence_Level = 'Human clinical',
    Evidence_Location = 'Human-volunteer skin-compatibility patch test (20 healthy volunteers; irritation/tolerability only, no clinical efficacy evaluation); paper also reports ex vivo human skin explants.'
WHERE Paper_ID = 'E09'
  AND Formulation_ID IN ('E09-F07','E09-F08');
