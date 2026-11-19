# Official adapter contracts and provenance notes

This note captures implementation-relevant facts verified against official source documentation on 2026-11-17. The source documentation is authoritative for endpoints, policies and licensing; retry, timeout, cache and local rate values noted below are conservative client settings, not provider guarantees.

## PubChem PUG REST / PUG View

Official docs: [PUG REST](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest), [PUG REST tutorial](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest-tutorial), [PUG View](https://pubchem.ncbi.nlm.nih.gov/docs/pug-view), [throttling](https://pubchem.ncbi.nlm.nih.gov/docs/dynamic-request-throttling), [data sources](https://pubchem.ncbi.nlm.nih.gov/docs/data-sources), [citation](https://pubchem.ncbi.nlm.nih.gov/docs/citation-guidelines).

Public HTTPS examples include `/rest/pug/compound/name/{name}/cids/JSON`, `/rest/pug/compound/cid/{cid}/property/MolecularFormula,InChI,InChIKey,CanonicalSMILES,IsomericSMILES,MolecularWeight/JSON`, `/rest/pug/compound/cid/{cid}/synonyms/JSON`, and `/rest/pug_view/data/compound/{cid}/JSON?heading=Experimental+Properties`. Live verification on 2026-11-17 confirmed PUG REST returns `InChI`, `InChIKey`, `SMILES` and `ConnectivitySMILES` for acetaminophen; its CID 1983 synonym record includes `Paracetamol`, `APAP`, `Tylenol` and `Panadol`. This is a compound synonym listing, not proof of a current product label or formulation. The API may return normalized field names rather than the requested alias. CID identifies a standardized PubChem compound record; SID identifies a depositor substance and must be retained when it matters. Name resolution can return multiple CIDs. Computed PUG REST properties and source-attributed PUG View annotations must remain distinct. SMILES strings, especially connectivity-only SMILES, are observations, not unique identifiers. The documented ceiling is 5 requests/s and throttling is dynamic; monitor `X-Throttling-Control`. PubChem has a 30-second server limit. Third-party annotations can have source-specific rights; no blanket licence applies to all contributed data. Preserve annotation/source citations.

## RxNorm / RxNav

Official docs: [RxNorm API overview](https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html), [name lookup](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.findRxcuiByString.html), [approximate matching](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getApproximateMatch.html), [all properties](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getAllProperties.html), [terms](https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html).

Public REST base: `https://rxnav.nlm.nih.gov/REST`. Exact/normalized search: `/rxcui.json?name=...&search=2`; fuzzy candidate search: `/approximateTerm.json?term=...&maxEntries=...`; `/rxcui/{rxcui}/properties.json` returns concept name and TTY directly, while `/allProperties.json?prop=names+codes` returns names/codes. An RxCUI is a concept ID and TTY distinguishes ingredient concepts from clinical drug/product concepts; attach a compound RxCUI only when TTY is `IN`. Search may return multiple candidates and vocabulary names can be withheld without UMLS rights. The documented limit is 20 requests/s/IP (100 with an optional UMLS key); NLM recommends caching 12–24 hours. RxNorm terms are public without a licence except for one stated exception; include NLM's requested non-endorsement attribution. RxNorm is US clinical terminology, not a research-chemical registry or literature search service.

## DailyMed API v2

Official docs: [API overview](https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm), [SPL list](https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/spls_api.cfm), [UNII index](https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/uniis_api.cfm), [SPL by SETID](https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/spls_setid_api.cfm), [about DailyMed](https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm).

Public GET JSON/XML base: `https://dailymed.nlm.nih.gov/dailymed/services/v2/`. Examples: `/uniis.json?active_moiety=simvastatin&pagesize=10&page=1`, `/spls.json?unii_code=...&pagesize=10&page=1`, and `/spls/{SETID}.xml`. SETID identifies a label set; `spl_version` tracks its version; UNII identifies an ingredient where available. Page size is capped at 100. The reviewed docs do not state a numerical rate limit, API licence or required authentication; do not invent one. Labels are submitted by companies; NLM does not review SPL content before publication, and the page may differ from the current FDA-approved/distributed label. This is label/index data, not a literature API or safety endorsement.

## openFDA Drug Product Labeling

Official docs: [label endpoint](https://open.fda.gov/apis/drug/label/), [search fields](https://open.fda.gov/apis/drug/label/searchable-fields/), [queries](https://open.fda.gov/apis/drug/label/example-api-queries/), [authentication](https://open.fda.gov/apis/authentication/), [terms](https://open.fda.gov/terms/).

Public endpoint: `https://api.fda.gov/drug/label.json`; example `?search=active_ingredient:acetaminophen&limit=5`. Useful source fields include `active_ingredient`, `inactive_ingredient`, label `id`/`set_id`, `openfda.unii`, `product_ndc`, `application_number`, `rxcui`, brand/generic names and manufacturer. No key is required, but documented quotas are 240 requests/minute and 1,000/day/IP without a key; with a key, 240/minute and 120,000/day. Unless otherwise noted, openFDA data/docs/code are CC0; attribution is requested, and third-party material can have separate rights. FDA says labeling is submitted by manufacturers/distributors, reformatted but not verified by FDA, and can differ from current approved/distributed labeling. It is not an ingredient ontology or literature source.

## NIH Office of Dietary Supplements DSLD

Official docs: [API guide](https://dsld.od.nih.gov/api-guide), [OpenAPI/Swagger](https://app.swaggerhub.com/apis/ODSDSLD/DSLD/9.4.0), [about DSLD](https://dsld.od.nih.gov/about), [database background](https://ods.od.nih.gov/Research/Dietary_Supplement_Label_Database.aspx).

Public v9 examples: `GET https://api.ods.od.nih.gov/dsld/v9/label/{id}`, `/ingredient-groups/?method=by_keyword&term=Vitamin%20D`, and `/search-filter?q=Strontium`. The stable label key is DSLD `id`; nested ingredient rows expose local `ingredientId`, label text, units, quantity and ingredient group. API guide states 1,000 requests/hour/IP without a key and 10,000/hour with a data.gov key. Data are CC0 1.0/public domain; ODS asks for attribution and no implied NIH endorsement. DSLD transcribes supplement labels, not independently verified composition, safety or efficacy. Ingredient IDs are DSLD-local; full-text-linked documents may have separate copyright.

## PubMed / NCBI E-utilities

Official docs: [NCBI API overview](https://www.ncbi.nlm.nih.gov/home/develop/api/), [E-utilities guide](https://www.ncbi.nlm.nih.gov/books/NBK25497/), [ESearch parameters](https://www.ncbi.nlm.nih.gov/books/NBK25499/), [NLM Data Guide](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html), [NCBI policies](https://www.ncbi.nlm.nih.gov/home/about/policies/).

Public base: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`. Use `esearch.fcgi?db=pubmed&term=...&retmode=json`, then `esummary.fcgi` for metadata or `efetch.fcgi` for XML. PMID is the PubMed record key; DOI/PMCID are optional cross-identifiers. NCBI documents 3 requests/s/IP without key, 10/s with key, encourages registered `tool` and `email`, and supports batching; ESearch returns at most 10,000 PubMed IDs. NLM requests visible attribution/disclaimer. Bibliographic metadata are distinct from abstracts and article content, which may be copyrighted. PubMed is a literature index, not a chemical identity authority; a mention is not proof of identity or performance.

## Europe PMC REST

Official docs: [REST guide](https://europepmc.org/RestfulWebService), [reference guide](https://europepmc.org/docs/EBI_Europe_PMC_Web_Service_Reference.pdf), [search syntax](https://europepmc.org/searchsyntax), [copyright](https://europepmc.org/Copyright), [fields](https://www.ebi.ac.uk/europepmc/webservices/rest/fields).

Public base: `https://www.ebi.ac.uk/europepmc/webservices/rest/`. Search examples use `/search?query=...&format=json&resultType=core`; searchable fields include `CHEM`, `CHEBITERM`, `CHEBI_PUBS`, DOI and external IDs. Store the `(source,id)` pair and preserve PMID, PMCID and DOI where supplied. Docs describe `cursorMark` pagination and `pageSize` up to 1,000; reviewed docs did not specify a numerical REST quota or mandatory auth. Europe PMC is a literature/metadata aggregator and its chemical annotations are not a canonical ingredient registry. Full-text article rights vary item-by-item; API access is not a blanket republication licence. Attribute Europe PMC plus the underlying article and its specific licence.

## Shared adapter policy

All adapters use HTTPS, a finite timeout, no more than two retries for transient network/408/429/5xx failures, exponential backoff, and a local response cache with retrieval timestamps. Ordinary 4xx errors are not retried. Provider-specific local rate ceilings stay below stated limits; where the source publishes no number, the client uses a conservative single-request-per-second ceiling. Persist original response text/JSON and provider record IDs. Keep candidate/matched/unavailable/error distinct. Public accessibility does not mean a record is verified or licensed for unrestricted display.
