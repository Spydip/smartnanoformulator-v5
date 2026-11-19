# Resolver acceptance-source checks

Lookups were made on 2026-11-17 through the source adapters. These checks support resolver examples; they do not verify product composition or establish clinical claims.

## Acetaminophen synonyms

PubChem PUG REST CID 1983 synonyms endpoint: <https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1983/synonyms/JSON>. Retrieved `2026-11-17T12:51:15Z`. The full response contained 1,716 synonym strings and included `Paracetamol`, `APAP`, `Tylenol`, and `Panadol`. Only the explicitly selected alias strings are used in the resolver; all such aliases remain pending human review. A synonym listing is not a current product-label record.

## Brand-label lookup

The openFDA and DailyMed adapters were queried by the literal brand names `Tylenol` and `Panadol` on 2026-11-17. For Tylenol, openFDA returned 20 label records and DailyMed returned 25. A retrieved DailyMed example is SETID `44e8a92a-748b-4dc3-ae8d-176a350ac83d`, title `TYLENOL PM EXTRA STRENGTH (ACETAMINOPHEN AND DIPHENHYDRAMINE HYDROCHLORIDE) SOLUTION`. For Panadol, openFDA returned 11 label records and DailyMed returned 11. A retrieved DailyMed example is SETID `341761b9-f0be-f816-e063-6394a90a5e63`, title `PANADOL COLD AND FLU DAY (ACETAMINOPHEN, DEXTROMETHORPHAN HBR) TABLET`.

The openFDA records expose active-ingredient text such as acetaminophen plus other ingredients; DailyMed titles expose combination-product labels. The UI therefore presents label-source results as product candidates and keeps the active-ingredient text separate. Neither the name search nor the label itself creates an automatic product-to-ingredient relation. FDA and DailyMed documentation notes that label content is submitted by manufacturers and is not independently verified by the API host; check the specific current product label before use.

Sources: openFDA Drug Product Label API, <https://api.fda.gov/drug/label.json>; DailyMed SPL API v2, <https://dailymed.nlm.nih.gov/dailymed/services/v2/>; official source and caveat notes in [`ADAPTER_CONTRACTS.md`](ADAPTER_CONTRACTS.md).
