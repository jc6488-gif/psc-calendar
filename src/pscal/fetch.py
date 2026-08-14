"""Polite HTTP layer: retries, timeouts, on-disk caching, shared session."""
from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("PSCAL_CACHE", ".cache"))
CACHE_TTL = int(os.environ.get("PSCAL_CACHE_TTL", "3600"))
TIMEOUT = (10, 30)  # connect, read

# Identify ourselves honestly. Several commissions block generic scraper UAs,
# and a contactable UA is the difference between being tolerated and being banned.
# The "Mozilla/5.0 (compatible; ...)" prefix is the standard crawler convention
# (Googlebot uses it) - it still names the tool and a contact, but several state
# WAFs (OH, CO, VT) reject any UA without the Mozilla prefix outright.
UA = os.environ.get(
    "PSCAL_USER_AGENT",
    "Mozilla/5.0 (compatible; psc-calendar/1.0; regulatory calendar aggregator; +https://github.com/)",
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/calendar;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


SESSION = _session()
_last_hit: dict[str, float] = {}
MIN_INTERVAL = 1.0  # seconds between requests to the same host


class FetchError(Exception):
    pass


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".bin")


def _throttle(url: str) -> None:
    try:
        host = requests.utils.urlparse(url).netloc
    except Exception:
        return
    now = time.monotonic()
    last = _last_hit.get(host)
    if last is not None:
        wait = MIN_INTERVAL - (now - last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.3))
    _last_hit[host] = time.monotonic()


def get(url: str, *, use_cache: bool = True, timeout=TIMEOUT,
        headers: dict | None = None) -> tuple[bytes, str]:
    """Fetch a URL. Returns (body_bytes, content_type). Raises FetchError."""
    cp = _cache_path(url + ("|" + str(sorted(headers.items())) if headers else ""))
    if use_cache and cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL:
        meta = cp.with_suffix(".ct")
        ct = meta.read_text().strip() if meta.exists() else ""
        log.debug("cache hit %s", url)
        return cp.read_bytes(), ct

    _throttle(url)
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True, headers=headers)
    except requests.RequestException as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e

    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    if not r.content:
        raise FetchError("empty response body")

    ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(r.content)
        cp.with_suffix(".ct").write_text(ct)
    return r.content, ct


def post(url: str, *, data=None, json=None, use_cache: bool = True,
         timeout=TIMEOUT, prime_url: str | None = None) -> tuple[bytes, str]:
    """POST to a URL (same throttle/UA/session as get). Some commission data
    endpoints only answer POST (MA DPU File Room, LA portal). `prime_url`
    optionally GETs a page first so the session collects cookies the endpoint
    requires. Cached by URL+body like get()."""
    import json as _json
    body_key = url + "|" + (_json.dumps(json, sort_keys=True) if json else str(sorted((data or {}).items())))
    cp = CACHE_DIR / (hashlib.sha1(body_key.encode()).hexdigest() + ".bin")
    if use_cache and cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL:
        meta = cp.with_suffix(".ct")
        return cp.read_bytes(), (meta.read_text().strip() if meta.exists() else "")

    if prime_url:
        try:
            _throttle(prime_url)
            SESSION.get(prime_url, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            pass
    _throttle(url)
    try:
        r = SESSION.post(url, data=data, json=json, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    if not r.content:
        raise FetchError("empty response body")
    ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(r.content)
        cp.with_suffix(".ct").write_text(ct)
    return r.content, ct


def get_text(url: str, **kw) -> tuple[str, str]:
    body, ct = get(url, **kw)
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return body.decode(enc), ct
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace"), ct
