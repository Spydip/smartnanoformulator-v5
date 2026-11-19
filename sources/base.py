"""Conservative, cache-first HTTP client shared by public data adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen

TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_response_cache (
    provider TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    request_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    fetched_epoch REAL NOT NULL,
    expires_epoch REAL NOT NULL,
    http_status INTEGER NOT NULL,
    content_type TEXT,
    response_body TEXT NOT NULL,
    PRIMARY KEY(provider, cache_key)
);
CREATE INDEX IF NOT EXISTS idx_api_cache_expiry ON api_response_cache(expires_epoch);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact_url(url: str) -> str:
    p = urlsplit(url)
    q = [(k, "REDACTED" if k.lower() in {"api_key", "apikey", "key"} else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def _default_transport(url: str, headers: dict[str, str], timeout: float):
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as response:
        return response.code, dict(response.headers.items()), response.read(MAX_RESPONSE_BYTES + 1)


@dataclass
class FetchResult:
    ok: bool
    status_code: int | None
    data: Any = None
    error: str | None = None
    retrieved_at: str | None = None
    request_url: str | None = None
    cache_hit: bool = False
    attempts: int = 0
    content_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RequestClient:
    """GET-only client with per-provider throttling, bounded retries and SQLite cache."""

    def __init__(self, cache_con: sqlite3.Connection | None = None,
                 transport: Callable | None = None, timeout: float = 15.0,
                 sleep: Callable[[float], None] = time.sleep,
                 wall_time: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.con = cache_con
        if self.con is not None:
            self.con.executescript(CACHE_SCHEMA)
        self.transport = transport or _default_transport
        self.timeout = float(timeout)
        self.sleep = sleep
        self.wall_time = wall_time
        self.monotonic = monotonic
        self._last_request: dict[str, float] = {}
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._memory_cache: dict[tuple[str, str], tuple[float, str, str, str | None]] = {}

    def _cache_get(self, provider: str, key: str):
        now = self.wall_time()
        if self.con is not None:
            with self._cache_lock:
                row = self.con.execute(
                    "SELECT response_body,fetched_at,expires_epoch,content_type FROM api_response_cache WHERE provider=? AND cache_key=?",
                    (provider, key),
                ).fetchone()
            if row and float(row[2]) > now:
                return str(row[0]), str(row[1]), row[3]
            return None
        row = self._memory_cache.get((provider, key))
        if row and row[0] > now:
            return row[1], row[2], row[3]
        return None

    def _cache_put(self, provider: str, key: str, request_url: str, body: str,
                   fetched_at: str, expires_epoch: float, status: int, content_type: str | None):
        fetched_epoch = self.wall_time()
        if self.con is not None:
            with self._cache_lock:
                self.con.execute(
                    "INSERT INTO api_response_cache(provider,cache_key,request_url,fetched_at,fetched_epoch,expires_epoch,http_status,content_type,response_body) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(provider,cache_key) DO UPDATE SET request_url=excluded.request_url,fetched_at=excluded.fetched_at,fetched_epoch=excluded.fetched_epoch,expires_epoch=excluded.expires_epoch,http_status=excluded.http_status,content_type=excluded.content_type,response_body=excluded.response_body",
                    (provider, key, request_url, fetched_at, fetched_epoch, expires_epoch, status, content_type, body),
                )
                self.con.commit()
        else:
            self._memory_cache[(provider, key)] = (expires_epoch, body, fetched_at, content_type)

    def _throttle(self, provider: str, min_interval: float):
        if min_interval <= 0:
            return
        with self._rate_lock:
            now = self.monotonic()
            previous = self._last_request.get(provider)
            delay = max(0.0, min_interval - (now - previous)) if previous is not None else 0.0
            if delay:
                self.sleep(delay)
            self._last_request[provider] = self.monotonic()

    def get_json(self, provider: str, base_url: str, params: dict[str, Any],
                 *, ttl_seconds: int, min_interval: float, timeout: float | None = None,
                 extra_headers: dict[str, str] | None = None) -> FetchResult:
        clean_params = {k: v for k, v in params.items() if v is not None}
        query = urlencode(clean_params, doseq=True)
        url = base_url + ("&" if "?" in base_url else "?") + query if query else base_url
        safe_url = _redact_url(url)
        cache_key = hashlib.sha256(safe_url.encode("utf-8")).hexdigest()
        cached = self._cache_get(provider, cache_key)
        if cached:
            body, fetched_at, content_type = cached
            try:
                return FetchResult(True, 200, json.loads(body), retrieved_at=fetched_at,
                                   request_url=safe_url, cache_hit=True, content_type=content_type)
            except json.JSONDecodeError:
                pass

        headers = {"Accept": "application/json", "User-Agent": "SmartNanoFormulator/5.0.2 (research prototype)"}
        headers.update(extra_headers or {})
        result: FetchResult | None = None
        attempts = 0
        for attempt in range(3):
            attempts = attempt + 1
            self._throttle(provider, min_interval)
            try:
                status, response_headers, raw = self.transport(url, headers, timeout or self.timeout)
                content_type = next((v for k, v in response_headers.items() if k.lower() == "content-type"), None)
                if status == 200:
                    body_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
                    if len(body_bytes) > MAX_RESPONSE_BYTES:
                        return FetchResult(False, status, error="Response exceeded the 10 MiB safety limit.",
                                           request_url=safe_url, attempts=attempts, content_type=content_type)
                    try:
                        body_text = body_bytes.decode("utf-8")
                        parsed = json.loads(body_text)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        return FetchResult(False, status, error=f"Invalid JSON response: {exc}", request_url=safe_url, attempts=attempts, content_type=content_type)
                    retrieved = utc_now()
                    self._cache_put(provider, cache_key, safe_url, body_text, retrieved,
                                    self.wall_time() + max(0, ttl_seconds), status, content_type)
                    return FetchResult(True, status, parsed, retrieved_at=retrieved,
                                       request_url=safe_url, attempts=attempts, content_type=content_type)
                retry_after = next((v for k, v in response_headers.items() if k.lower() == "retry-after"), None)
                message = f"HTTP {status}" + (f"; {raw[:240].decode('utf-8', errors='replace')}" if isinstance(raw, bytes) else "")
                result = FetchResult(False, status, error=message, request_url=safe_url, attempts=attempts, content_type=content_type)
                if status not in TRANSIENT_HTTP or attempt == 2:
                    return result
                try:
                    delay = min(max(float(retry_after), 0.0), 8.0) if retry_after else 0.5 * (2 ** attempt)
                except ValueError:
                    delay = 0.5 * (2 ** attempt)
                self.sleep(delay)
            except (TimeoutError, OSError, URLError) as exc:
                result = FetchResult(False, None, error=f"Network error: {exc.__class__.__name__}: {exc}",
                                     request_url=safe_url, attempts=attempts)
                if attempt == 2:
                    return result
                self.sleep(0.5 * (2 ** attempt))
        return result or FetchResult(False, None, error="Request failed.", request_url=safe_url, attempts=attempts)

    def get_text(self, provider: str, base_url: str, params: dict[str, Any],
                 *, ttl_seconds: int, min_interval: float, timeout: float | None = None,
                 accept: str = "application/xml,text/plain;q=0.9,*/*;q=0.5",
                 extra_headers: dict[str, str] | None = None) -> FetchResult:
        """GET and cache a bounded raw text/XML response using the shared policy."""
        clean_params = {k: v for k, v in params.items() if v is not None}
        query = urlencode(clean_params, doseq=True)
        url = base_url + ("&" if "?" in base_url else "?") + query if query else base_url
        safe_url = _redact_url(url)
        cache_key = hashlib.sha256((safe_url + "|text").encode("utf-8")).hexdigest()
        cached = self._cache_get(provider, cache_key)
        if cached:
            body, fetched_at, content_type = cached
            return FetchResult(True, 200, body, retrieved_at=fetched_at, request_url=safe_url,
                               cache_hit=True, content_type=content_type)
        headers = {"Accept": accept, "User-Agent": "SmartNanoFormulator/5.0.2 (research prototype)"}
        headers.update(extra_headers or {})
        for attempt in range(3):
            self._throttle(provider, min_interval)
            try:
                status, response_headers, raw = self.transport(url, headers, timeout or self.timeout)
                content_type = next((v for k, v in response_headers.items() if k.lower() == "content-type"), None)
                body = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
                if len(body) > MAX_RESPONSE_BYTES:
                    return FetchResult(False, status, error="Response exceeded the 10 MiB safety limit.",
                                       request_url=safe_url, attempts=attempt + 1, content_type=content_type)
                if status == 200:
                    text = body.decode("utf-8", errors="replace")
                    retrieved = utc_now()
                    self._cache_put(provider, cache_key, safe_url, text, retrieved,
                                    self.wall_time() + max(0, ttl_seconds), status, content_type)
                    return FetchResult(True, status, text, retrieved_at=retrieved,
                                       request_url=safe_url, attempts=attempt + 1, content_type=content_type)
                message = f"HTTP {status}" + (f"; {body[:240].decode('utf-8', errors='replace')}" if body else "")
                result = FetchResult(False, status, error=message, request_url=safe_url,
                                     attempts=attempt + 1, content_type=content_type)
                if status not in TRANSIENT_HTTP or attempt == 2:
                    return result
                retry_after = next((v for k, v in response_headers.items() if k.lower() == "retry-after"), None)
                try:
                    delay = min(max(float(retry_after), 0.0), 8.0) if retry_after else 0.5 * (2 ** attempt)
                except ValueError:
                    delay = 0.5 * (2 ** attempt)
                self.sleep(delay)
            except (TimeoutError, OSError, URLError) as exc:
                result = FetchResult(False, None, error=f"Network error: {exc.__class__.__name__}: {exc}",
                                     request_url=safe_url, attempts=attempt + 1)
                if attempt == 2:
                    return result
                self.sleep(0.5 * (2 ** attempt))
        return result


@dataclass
class AdapterResult:
    provider: str
    status: str
    query: str
    records: list[dict[str, Any]]
    retrieved_at: str | None
    attribution: str
    licence_note: str
    request_url: str | None = None
    cache_hit: bool = False
    error: str | None = None
    raw_response: Any = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def records_result(provider: str, query: str, fetch: FetchResult, records: list[dict[str, Any]],
                    attribution: str, licence_note: str, *, ambiguous: bool | None = None) -> AdapterResult:
    if not fetch.ok:
        status = "not_found" if fetch.status_code == 404 else "error"
    elif not records:
        status = "not_found"
    elif ambiguous is True or (ambiguous is None and len(records) > 1):
        status = "ambiguous"
    else:
        status = "found"
    return AdapterResult(provider, status, query, records, fetch.retrieved_at, attribution,
                         licence_note, fetch.request_url, fetch.cache_hit, fetch.error, fetch.data)
