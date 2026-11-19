from __future__ import annotations
import os
from .base import RequestClient, AdapterResult, records_result

BASE = "https://api.fda.gov/drug/label.json"
ATTRIBUTION = "Data provided by the U.S. Food and Drug Administration (FDA), openFDA."
LICENCE = "openFDA data are generally CC0 unless otherwise noted; third-party content may have separate rights."


class OpenFDAAdapter:
    provider = "openfda"
    min_interval = 0.25  # 4 requests/second, below the documented 240/minute quota.
    ttl = 86400

    def __init__(self, client: RequestClient, api_key: str | None = None):
        self.client = client
        self.api_key = api_key if api_key is not None else os.getenv("OPENFDA_API_KEY")

    def _search(self, field: str, term: str, limit: int):
        query = str(term).strip().replace('"', '\\"')
        params = {"search": f'{field}:"{query}"', "limit": max(1, min(int(limit), 100))}
        if self.api_key:
            params["api_key"] = self.api_key
        fetch = self.client.get_json(self.provider, BASE, params, ttl_seconds=self.ttl, min_interval=self.min_interval)
        records = fetch.data.get("results", []) if fetch.ok and isinstance(fetch.data, dict) else []
        return records_result(self.provider, f"{field}:{term}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def search_ingredient(self, term: str, role: str = "active", limit: int = 20) -> AdapterResult:
        field = "active_ingredient" if role.lower() == "active" else "inactive_ingredient"
        return self._search(field, term, limit)

    def search_unii(self, unii: str, limit: int = 20) -> AdapterResult:
        return self._search("openfda.unii", unii, limit)

    def search_brand(self, brand: str, limit: int = 20) -> AdapterResult:
        return self._search("openfda.brand_name", brand, limit)

    def search_rxcui(self, rxcui: str, limit: int = 20) -> AdapterResult:
        return self._search("openfda.rxcui", rxcui, limit)
