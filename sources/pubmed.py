from __future__ import annotations
import os
import xml.etree.ElementTree as ET
from .base import RequestClient, AdapterResult, records_result

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ATTRIBUTION = "PubMed, National Center for Biotechnology Information (NCBI), U.S. National Library of Medicine (NLM)."
LICENCE = "Bibliographic metadata are distinct from abstracts/full text, which may be copyrighted by publishers or authors."


class PubMedAdapter:
    provider = "pubmed"
    ttl = 7 * 86400

    def __init__(self, client: RequestClient, tool: str | None = None,
                 email: str | None = None, api_key: str | None = None):
        self.client = client
        self.tool = tool if tool is not None else os.getenv("NCBI_TOOL", "SmartNanoFormulator")
        self.email = email if email is not None else os.getenv("NCBI_EMAIL")
        self.api_key = api_key if api_key is not None else os.getenv("NCBI_API_KEY")
        self.min_interval = 0.11 if self.api_key else 0.34  # below 10/s with key or 3/s without.

    def _params(self, params: dict):
        params = dict(params)
        params["tool"] = self.tool
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _json(self, endpoint: str, params: dict):
        return self.client.get_json(self.provider, BASE + "/" + endpoint,
                                    self._params(params), ttl_seconds=self.ttl,
                                    min_interval=self.min_interval)

    def search(self, term: str, retmax: int = 100) -> AdapterResult:
        query = str(term).strip()
        fetch = self._json("esearch.fcgi", {"db":"pubmed", "term":query,
                                               "retmax":max(1, min(int(retmax), 10000)), "retmode":"json"})
        result = fetch.data.get("esearchresult", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        ids = result.get("idlist", [])
        records = [{"pmid":str(pmid), "query":query} for pmid in ids]
        return records_result(self.provider, query, fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def summary(self, pmids: list[str | int]) -> AdapterResult:
        ids = [str(x) for x in pmids[:200] if str(x).isdigit()]
        if not ids:
            return AdapterResult(self.provider, "not_found", "PMID summary", [], None, ATTRIBUTION, LICENCE,
                                 error="No valid PMIDs supplied.")
        fetch = self._json("esummary.fcgi", {"db":"pubmed", "id":",".join(ids), "retmode":"json", "version":"2.0"})
        result = fetch.data.get("result", {}) if fetch.ok and isinstance(fetch.data, dict) else {}
        records = [dict(result[uid], pmid=str(uid)) for uid in result.get("uids", []) if isinstance(result.get(uid), dict)]
        return records_result(self.provider, "PMID:" + ",".join(ids), fetch, records, ATTRIBUTION, LICENCE, ambiguous=False)

    def fetch_xml(self, pmids: list[str | int]) -> AdapterResult:
        """Retrieve PubMed XML through EFetch while preserving the original XML text."""
        ids = [str(x) for x in pmids[:200] if str(x).isdigit()]
        if not ids:
            return AdapterResult(self.provider, "not_found", "PMID XML", [], None, ATTRIBUTION, LICENCE, error="No valid PMIDs supplied.")
        params = self._params({"db":"pubmed", "id":",".join(ids), "retmode":"xml"})
        fetch = self.client.get_text(self.provider, BASE + "/efetch.fcgi", params,
                                     ttl_seconds=self.ttl, min_interval=self.min_interval,
                                     accept="application/xml,text/xml")
        if not fetch.ok:
            status = "not_found" if fetch.status_code == 404 else "error"
            return AdapterResult(self.provider, status, "PMID:" + ",".join(ids), [],
                                 fetch.retrieved_at, ATTRIBUTION, LICENCE,
                                 fetch.request_url, fetch.cache_hit, fetch.error)
        try:
            text = str(fetch.data)
            root = ET.fromstring(text)
            records = []
            for article in root.findall(".//PubmedArticle"):
                pmid = article.findtext(".//PMID")
                title = " ".join((article.findtext(".//ArticleTitle") or "").split())
                abstract_parts = []
                for item in article.findall(".//Abstract/AbstractText"):
                    section_text = " ".join(" ".join(x for x in item.itertext() if x).split())
                    if section_text:
                        abstract_parts.append(section_text)
                abstract = " ".join(abstract_parts)
                records.append({"pmid":pmid, "title":title, "abstract":abstract, "raw_xml_article":ET.tostring(article, encoding="unicode")})
            return AdapterResult(self.provider, "found" if records else "not_found", "PMID:" + ",".join(ids), records,
                                 fetch.retrieved_at, ATTRIBUTION, LICENCE, fetch.request_url,
                                 fetch.cache_hit, raw_response=text)
        except ET.ParseError as exc:
            return AdapterResult(self.provider, "error", "PMID:" + ",".join(ids), [],
                                 fetch.retrieved_at, ATTRIBUTION, LICENCE, fetch.request_url,
                                 fetch.cache_hit, error=f"Invalid PubMed XML: {exc}", raw_response=fetch.data)
