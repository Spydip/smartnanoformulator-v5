from __future__ import annotations
from urllib.parse import quote
from .base import RequestClient, AdapterResult, records_result

BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
ATTRIBUTION = "DailyMed, U.S. National Library of Medicine (NLM), National Institutes of Health."
LICENCE = "The reviewed DailyMed API documentation states no blanket API reuse licence; verify applicable rights before redistribution."


class DailyMedAdapter:
    provider = "dailymed"
    min_interval = 1.0  # Conservative client pacing; the reviewed docs publish no numeric quota.
    ttl = 86400

    def __init__(self, client: RequestClient):
        self.client = client

    def _get(self, path: str, params: dict | None = None):
        return self.client.get_json(self.provider, BASE + path, params or {}, ttl_seconds=self.ttl,
                                    min_interval=self.min_interval)

    @staticmethod
    def _data(fetch):
        return fetch.data.get("data", []) if fetch.ok and isinstance(fetch.data, dict) else []

    def search_ingredients(self, active_moiety: str, page_size: int = 25, page: int = 1) -> AdapterResult:
        query = str(active_moiety).strip()
        fetch = self._get("/uniis.json", {"active_moiety": query, "pagesize": max(1, min(int(page_size), 100)), "page": max(1, int(page))})
        return records_result(self.provider, query, fetch, self._data(fetch), ATTRIBUTION, LICENCE, ambiguous=len(self._data(fetch)) > 1)

    def search_labels(self, term: str, page_size: int = 25, page: int = 1) -> AdapterResult:
        query = str(term).strip()
        fetch = self._get("/spls.json", {"drug_name": query, "pagesize": max(1, min(int(page_size), 100)), "page": max(1, int(page))})
        return records_result(self.provider, query, fetch, self._data(fetch), ATTRIBUTION, LICENCE, ambiguous=len(self._data(fetch)) > 1)

    def search_by_unii(self, unii: str, page_size: int = 25, page: int = 1) -> AdapterResult:
        query = str(unii).strip()
        fetch = self._get("/spls.json", {"unii_code": query, "pagesize": max(1, min(int(page_size), 100)), "page": max(1, int(page))})
        return records_result(self.provider, f"UNII:{query}", fetch, self._data(fetch), ATTRIBUTION, LICENCE, ambiguous=len(self._data(fetch)) > 1)

    def get_label_xml(self, setid: str) -> AdapterResult:
        return self.get_label_xml_text(setid)

    def get_label_xml_text(self, setid: str):
        """Fetch and cache raw public SPL XML while retaining source provenance."""
        path = f"{BASE}/spls/{quote(str(setid), safe='')}.xml"
        fetch = self.client.get_text(self.provider, path, {}, ttl_seconds=self.ttl,
                                     min_interval=self.min_interval, accept="application/xml")
        if not fetch.ok:
            status = "not_found" if fetch.status_code == 404 else "error"
            return AdapterResult(self.provider, status, f"SETID:{setid}", [], fetch.retrieved_at,
                                 ATTRIBUTION, LICENCE, fetch.request_url, fetch.cache_hit, fetch.error)
        return AdapterResult(self.provider, "found", f"SETID:{setid}",
                             [{"setid":str(setid), "spl_xml":fetch.data}], fetch.retrieved_at,
                             ATTRIBUTION, LICENCE, fetch.request_url, fetch.cache_hit, raw_response=fetch.data)
