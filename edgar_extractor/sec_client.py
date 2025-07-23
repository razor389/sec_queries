from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

SEC_BASE = "https://www.sec.gov"
_CACHE_DIR = Path(".cache")
_TICKER_CACHE = _CACHE_DIR / "ticker_cik_cache.json"
_JSON_TABLE = _CACHE_DIR / "company_tickers.json"


def _save_cache(cache: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _TICKER_CACHE.write_text(json.dumps(cache, indent=2))


def _load_cache() -> dict:
    if _TICKER_CACHE.exists():
        return json.loads(_TICKER_CACHE.read_text())
    return {}


def _load_sec_ticker_table(session: requests.Session, headers: dict) -> dict:
    """
    Download SEC's official ticker table once and cache it.
    https://www.sec.gov/files/company_tickers.json
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _JSON_TABLE.exists():
        return json.loads(_JSON_TABLE.read_text())

    url = "https://www.sec.gov/files/company_tickers.json"
    r = session.get(url, headers=headers, timeout=(10, 30))
    r.raise_for_status()
    data = r.json()
    _JSON_TABLE.write_text(json.dumps(data, indent=2))
    return data


class SECClient:
    def __init__(self, user_agent: str):
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json, text/html;q=0.9",
        }
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

    def get_cik_from_ticker(self, ticker: str) -> str:
        ticker = ticker.upper()
        cache = _load_cache()
        if ticker in cache:
            return cache[ticker]

        # Prefer JSON table
        try:
            table = _load_sec_ticker_table(self.session, self.headers)
            for _, row in table.items():
                if row["ticker"].upper() == ticker:
                    cik = str(row["cik_str"]).lstrip("0")
                    cache[ticker] = cik
                    _save_cache(cache)
                    return cik
        except requests.HTTPError as e:
            logger.warning("JSON ticker table failed (%s). Falling back to HTML scrape.", e)

        # HTML scrape fallback
        params = {"action": "getcompany", "CIK": ticker, "owner": "exclude", "count": "1"}
        url = f"{SEC_BASE}/cgi-bin/browse-edgar?{urlencode(params)}"
        r = self.session.get(url, headers=self.headers, timeout=(10, 30))
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cik_span = soup.find("span", string=lambda s: s and "CIK#" in s)
        if not cik_span:
            raise ValueError(f"Could not locate CIK for {ticker}")
        m = re.search(r"CIK#?:?\s*(\d+)", cik_span.get_text())
        if not m:
            raise ValueError(f"CIK not found in span for {ticker}")
        cik = m.group(1).lstrip("0")

        cache[ticker] = cik
        _save_cache(cache)
        return cik

    def list_filings(self, cik: str, form: str = "10-K", count: int = 10) -> List[Dict]:
        url = urljoin(
            SEC_BASE,
            f"/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type={form}&owner=exclude&start=0&count={count}&output=atom",
        )
        r = self.session.get(url, headers=self.headers, timeout=(10, 30))
        r.raise_for_status()

        root = BeautifulSoup(r.content, "xml")
        out: List[Dict] = []
        for entry in root.find_all("entry"):
            id_elem = entry.find("id")
            updated = entry.find("updated")
            link_elem = entry.find("link")
            accession = ""
            if id_elem and id_elem.text:
                m = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", id_elem.text)
                if m:
                    accession = m.group(1)
            href = link_elem.get("href") if link_elem else ""
            if accession and href:
                out.append({
                    "accession": accession,
                    "filing_url": urljoin(SEC_BASE, href),
                    "date": (updated.text.split("T")[0] if updated else ""),
                })
        return out

    def get_instance_xml_url(self, filing_url: str) -> str:
        r = self.session.get(filing_url, headers=self.headers, timeout=(10, 30))
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table", class_="tableFile")
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                desc = cells[1].get_text(strip=True).lower()
                if ("extracted" in desc and "instance document" in desc and "xbrl" in desc):
                    a = cells[2].find("a")
                    if a and a.get("href", "").lower().endswith(".xml"):
                        return urljoin(SEC_BASE, a["href"])

        a = soup.select_one('a[href$="_htm.xml"]')
        if a:
            return urljoin(SEC_BASE, a.get("href"))

        raise ValueError("No XML instance document link found on filing page.")

    def fetch_xml(self, xml_url: str) -> str:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = self.session.get(xml_url, headers=self.headers, timeout=(10, 60))
                r.raise_for_status()
                return r.text
            except requests.exceptions.Timeout:
                logger.warning("Timeout fetching XML (attempt %s/%s)", attempt + 1, max_retries)
                time.sleep(2)
        raise RuntimeError("Failed to fetch XML after retries")