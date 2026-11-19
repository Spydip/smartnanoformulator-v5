from __future__ import annotations
from urllib.parse import quote
from .base import RequestClient, AdapterResult, records_result

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest"
ATTRIBUTION = "PubChem, National Center for Biotechnology Information (NCBI), U.S. National Library of Medicine (NLM)."
LICENCE = "PubChem integrates third-party sources; check and retain source-specific reuse terms. No blanket licence is assumed."


class PubChemAdapter:
    provider = "pubchem"
    min_interval = 0.25  # 4 requests/second, below the documented 5/s ceiling.
    ttl = 7 * 86400

    def __init__(self, client: RequestClient):
        self.client = client

    def _get(self, path: str, params: dict | None = None):
        return self.client.get_json(self.provider, BASE + path, params or {}, ttl_seconds=self.ttl,
                                    min_interval=self.min_interval, timeout=30)

    def search_name(self, name: str) -> AdapterResult:
        query = str(name).strip()
        fetch = self._get(f"/pug/compound/name/{quote(query, safe='')}/cids/JSON")
        ids = fetch.data.get("IdentifierList", {}).get("CID", []) if fetch.ok and isinstance(fetch.data, dict) else []
        records = [{"cid": int(cid), "identifier_type": "PubChem CID", "input_name": query} for cid in ids]
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def search_identifier(self, identifier: str, identifier_type: str = "name") -> AdapterResult:
        query = str(identifier).strip()
        namespace = {"cid":"cid", "sid":"sid", "name":"name", "inchikey":"inchikey", "smiles":"smiles"}.get(identifier_type.lower())
        if namespace is None:
            return records_result(self.provider, query, self._unsupported(), [], ATTRIBUTION, LICENCE)
        fetch = self._get(f"/pug/compound/{namespace}/{quote(query, safe='')}/cids/JSON")
        ids = fetch.data.get("IdentifierList", {}).get("CID", []) if fetch.ok and isinstance(fetch.data, dict) else []
        records = [{"cid": int(cid), "identifier_type": "PubChem CID", "input_identifier": query, "input_type": identifier_type} for cid in ids]
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def _unsupported(self):
        from .base import FetchResult
        return FetchResult(False, 400, error="Unsupported PubChem identifier type.")

    def get_properties(self, cid: int | str) -> AdapterResult:
        wanted = "MolecularFormula,InChI,InChIKey,CanonicalSMILES,IsomericSMILES,ConnectivitySMILES,MolecularWeight,XLogP,TPSA,HBondDonorCount,HBondAcceptorCount,RotatableBondCount"
        fetch = self._get(f"/pug/compound/cid/{quote(str(cid), safe='')}/property/{wanted}/JSON")
        records = fetch.data.get("PropertyTable", {}).get("Properties", []) if fetch.ok and isinstance(fetch.data, dict) else []
        return records_result(self.provider, f"CID:{cid}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def get_synonyms(self, cid: int | str) -> AdapterResult:
        fetch = self._get(f"/pug/compound/cid/{quote(str(cid), safe='')}/synonyms/JSON")
        information = fetch.data.get("InformationList", {}).get("Information", []) if fetch.ok and isinstance(fetch.data, dict) else []
        records = [{"CID": int(cid), "Synonym": item.get("Synonym", [])} for item in information if isinstance(item, dict)]
        return records_result(self.provider, f"CID:{cid}; synonyms", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def get_annotations(self, cid: int | str, heading: str = "Experimental Properties") -> AdapterResult:
        fetch = self._get(f"/pug_view/data/compound/{quote(str(cid), safe='')}/JSON", {"heading": heading})
        records = [fetch.data] if fetch.ok and isinstance(fetch.data, dict) else []
        return records_result(self.provider, f"CID:{cid}; heading:{heading}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)
