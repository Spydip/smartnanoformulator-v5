from __future__ import annotations
from .base import RequestClient, AdapterResult, records_result

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ATTRIBUTION = "Europe PMC, EMBL-EBI; cite the underlying article and its own licence where content is reused."
LICENCE = "Article and full-text rights vary by record; API access does not grant a blanket content reuse licence."


class EuropePMCAdapter:
    provider = "europepmc"
    min_interval = 1.0  # Conservative pacing; reviewed docs specify no numeric public quota.
    ttl = 7 * 86400

    def __init__(self, client: RequestClient):
        self.client = client

    def _search(self, query: str, page_size: int, result_type: str):
        fetch = self.client.get_json(self.provider, BASE,
                                     {"query":query, "format":"json", "resultType":result_type,
                                      "pageSize":max(1, min(int(page_size), 1000))},
                                     ttl_seconds=self.ttl, min_interval=self.min_interval)
        data = fetch.data if fetch.ok and isinstance(fetch.data, dict) else {}
        records = data.get("resultList", {}).get("result", []) or []
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def search_chemical(self, term: str, page_size: int = 25, result_type: str = "core") -> AdapterResult:
        # CHEM is controlled MeSH substance indexing; CHEBITERM is text-mined.
        value = str(term).strip().replace('"', '\\"')
        query = f'CHEM:"{value}" OR CHEBITERM:"{value}"'
        return self._search(query, page_size, result_type)

    def search(self, query: str, page_size: int = 25, result_type: str = "core") -> AdapterResult:
        return self._search(str(query).strip(), page_size, result_type)

    def by_doi(self, doi: str) -> AdapterResult:
        q = "DOI:" + str(doi).strip()
        return self._search(q, 10, "core")

    def by_external_id(self, external_id: str, source: str) -> AdapterResult:
        q = f"EXT_ID:{str(external_id).strip()} AND SRC:{str(source).strip()}"
        return self._search(q, 10, "core")
