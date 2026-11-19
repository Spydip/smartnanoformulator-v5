from __future__ import annotations
from .base import RequestClient, AdapterResult, records_result

BASE = "https://rxnav.nlm.nih.gov/REST"
ATTRIBUTION = "This product uses publicly available data from the U.S. National Library of Medicine (NLM), NIH; NLM does not endorse or recommend this product."
LICENCE = "RxNorm is non-proprietary; separately returned third-party vocabulary content may have additional rights or limitations."


class RxNormAdapter:
    provider = "rxnorm"
    min_interval = 0.11  # 9 requests/second, below the documented 20/s/IP ceiling.
    ttl = 12 * 3600  # NLM recommends cache periods of 12–24 hours.

    def __init__(self, client: RequestClient):
        self.client = client

    def _get(self, path: str, params: dict | None = None):
        return self.client.get_json(self.provider, BASE + path, params or {}, ttl_seconds=self.ttl,
                                    min_interval=self.min_interval)

    def search_name(self, name: str, search: int = 2, allsrc: int = 0) -> AdapterResult:
        query = str(name).strip()
        fetch = self._get("/rxcui.json", {"name": query, "search": search, "allsrc": allsrc})
        ids = fetch.data.get("idGroup", {}).get("rxnormId", []) if fetch.ok and isinstance(fetch.data, dict) else []
        records = [{"rxcui": str(r), "query": query} for r in ids]
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def approximate_name(self, name: str, max_entries: int = 10) -> AdapterResult:
        query = str(name).strip()
        n = max(1, min(int(max_entries), 100))
        fetch = self._get("/approximateTerm.json", {"term": query, "maxEntries": n})
        group = fetch.data.get("approximateGroup", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        records = group.get("candidate", []) or []
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def get_properties(self, rxcui: str, prop: str = "names codes") -> AdapterResult:
        fetch = self._get(f"/rxcui/{str(rxcui)}/allProperties.json", {"prop": prop})
        group = fetch.data.get("propConceptGroup", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        records = group.get("propConcept", []) or []
        return records_result(self.provider, f"RxCUI:{rxcui}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def get_concept_properties(self, rxcui: str) -> AdapterResult:
        fetch = self._get(f"/rxcui/{str(rxcui)}/properties.json")
        record = fetch.data.get("properties", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        records = [record] if record else []
        return records_result(self.provider, f"RxCUI:{rxcui}; concept properties", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def related(self, rxcui: str, tty: str = "IN PIN MIN") -> AdapterResult:
        fetch = self._get(f"/rxcui/{str(rxcui)}/related.json", {"tty": tty})
        group = fetch.data.get("relatedGroup", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        records = group.get("conceptGroup", []) or []
        return records_result(self.provider, f"RxCUI:{rxcui}; TTY:{tty}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)
