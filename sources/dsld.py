from __future__ import annotations
import os
from .base import RequestClient, AdapterResult, records_result

BASE = "https://api.ods.od.nih.gov/dsld/v9"
ATTRIBUTION = "National Institutes of Health, Office of Dietary Supplements, Dietary Supplement Label Database (DSLD)."
LICENCE = "DSLD data are public domain/CC0 1.0; attribute ODS and do not imply NIH endorsement. Linked article/document rights remain separate."


class DSLDAdapter:
    provider = "dsld"
    ttl = 7 * 86400

    def __init__(self, client: RequestClient, api_key: str | None = None):
        self.client = client
        self.api_key = api_key if api_key is not None else os.getenv("DSLD_API_KEY")
        self.min_interval = 0.37 if self.api_key else 3.61  # 10,000 or 1,000 requests/hour per key/IP.

    def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        if self.api_key:
            params["api_key"] = self.api_key
        return self.client.get_json(self.provider, BASE + path, params, ttl_seconds=self.ttl,
                                    min_interval=self.min_interval)

    def _label_records(self, fetch):
        data = fetch.data if fetch.ok else None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("hits", "results", "labels", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            if data.get("id") is not None:
                return [data]
        return []

    def search_labels(self, term: str) -> AdapterResult:
        query = str(term).strip()
        fetch = self._get("/search-filter", {"q": query})
        records = self._label_records(fetch)
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def ingredient_group_search(self, term: str, method: str = "by_keyword") -> AdapterResult:
        query = str(term).strip()
        if method not in {"by_keyword", "by_letter", "factsheet"}:
            return AdapterResult(self.provider, "error", query, [], None, ATTRIBUTION, LICENCE,
                                 error="Unsupported DSLD ingredient-group method.")
        fetch = self._get("/ingredient-groups/", {"method": method, "term": query})
        records = self._label_records(fetch)
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=len(records) > 1)

    def get_label(self, label_id: str | int) -> AdapterResult:
        fetch = self._get(f"/label/{label_id}")
        records = [fetch.data] if fetch.ok and isinstance(fetch.data, dict) else []
        return records_result(self.provider, f"DSLD label:{label_id}", fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)
