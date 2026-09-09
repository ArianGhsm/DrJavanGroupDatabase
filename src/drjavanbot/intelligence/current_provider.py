from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
import ipaddress
import socket
from urllib.parse import quote, quote_plus, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from .models import EvidenceItem, FreshnessClass, RetrievalRequest, RetrievalResult, SourceType

_USER_AGENT = "Mozilla/5.0 (compatible; DrJavanBot/0.7; +https://github.com/ArianGhsm/DrJavanGroupDatabase)"
_MAX_PAGE_BYTES = 450_000


class CurrentInformationProvider:
    source_type = SourceType.CURRENT_WEB

    def __init__(self, *, timeout_seconds: float = 9.0, max_queries: int = 4, max_page_fetches: int = 8) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_queries = max(1, int(max_queries))
        self.max_page_fetches = max(0, int(max_page_fetches))

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.source_type != self.source_type:
            raise ValueError("current provider received mismatched source request")
        started = time.perf_counter()
        query_count = 0
        raw: list[dict] = []
        seen: set[str] = set()
        try:
            seed_raw: list[dict] = []
            for seed_url in _trusted_seed_urls(request):
                # Subclasses (especially OFFICIAL) must apply their own domain
                # admission policy to seeds too; a trusted current source must
                # never be relabelled as an official authority.
                if not self._admit_url(seed_url, request):
                    continue
                key = _canonical_url(seed_url)
                if key and key not in seen:
                    seen.add(key); seed_raw.append({"title":"", "link":seed_url, "description":"", "pub_date":"", "query":"trusted_seed"})
            if seed_raw:
                self._hydrate_pages(seed_raw)
                seed_items = [self._to_evidence(item, request, rank=i + 1) for i, item in enumerate(seed_raw)]
                seed_items = [item for item in seed_items if item is not None and _admit_current_evidence(item, request)]
                if seed_items and set(request.facets) & {"salary", "career"}:
                    seed_items.sort(key=lambda item: (float(item.trust_score or 0), float(item.retrieval_score or 0)), reverse=True)
                    return RetrievalResult(source_type=self.source_type, items=tuple(seed_items[:request.top_k]), query_count=0,
                                           latency_ms=(time.perf_counter() - started) * 1000.0)
                raw.extend(seed_raw)
            for query_text in self._query_texts(request)[: self.max_queries]:
                query_count += 1
                discovered = self._search_brave_html(query_text)
                if not discovered:
                    discovered = self._search_bing_rss(query_text)
                for item in discovered:
                    link = item.get("link") or ""
                    if not self._admit_url(link, request):
                        continue
                    key = _canonical_url(link)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    item["query"] = query_text
                    raw.append(item)
                    if len(raw) >= max(request.top_k * 2, 16):
                        break
                if len(raw) >= max(request.top_k * 2, 16):
                    break
            self._hydrate_pages(raw[: self.max_page_fetches])
            items = [self._to_evidence(item, request, rank=i + 1) for i, item in enumerate(raw)]
            items = [item for item in items if item is not None and _admit_current_evidence(item, request)]
            items.sort(key=lambda item: (float(item.trust_score or 0), float(item.retrieval_score or 0)), reverse=True)
            return RetrievalResult(
                source_type=self.source_type, items=tuple(items[: request.top_k]), query_count=query_count,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            return RetrievalResult(
                source_type=self.source_type, items=(), query_count=query_count,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                unavailable_reason=f"current_{type(exc).__name__.casefold()}",
            )

    def _query_texts(self, request: RetrievalRequest) -> tuple[str, ...]:
        return tuple(query.text for query in request.queries if query.text.strip())

    def _admit_url(self, url: str, request: RetrievalRequest) -> bool:
        return _public_http_url(url) and "bing.com" not in urlparse(url).netloc.casefold()

    def _search_brave_html(self, query: str) -> tuple[dict, ...]:
        url = "https://search.brave.com/search?q=" + quote_plus(query) + "&source=web"
        req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept-Language":"fa,en;q=0.8", "Accept":"text/html"})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                html = response.read(800_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            return ()
        soup = BeautifulSoup(html, "html.parser")
        out: list[dict] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not href.startswith("http"):
                continue
            domain = urlparse(href).netloc.casefold().removeprefix("www.")
            if not domain or "brave.com" in domain or href in seen:
                continue
            seen.add(href)
            title = " ".join(anchor.get_text(" ", strip=True).split())
            parent = anchor.parent
            snippet = ""
            for _ in range(3):
                if parent is None: break
                candidate = " ".join(parent.get_text(" ", strip=True).split())
                if len(candidate) > len(title) + 25:
                    snippet = candidate[:900]; break
                parent = parent.parent
            out.append({"title":title, "link":href, "description":snippet, "pub_date":""})
            if len(out) >= 12:
                break
        return tuple(out)

    def _search_bing_rss(self, query: str) -> tuple[dict, ...]:
        url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
        req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
        with urlopen(req, timeout=self.timeout_seconds) as response:
            payload = response.read(1_000_000)
        root = ET.fromstring(payload)
        out: list[dict] = []
        for item in root.findall(".//item"):
            out.append({
                "title": _xml_text(item.find("title")), "link": _xml_text(item.find("link")),
                "description": _strip_html(_xml_text(item.find("description"))),
                "pub_date": _xml_text(item.find("pubDate")),
            })
        return tuple(out)

    def _hydrate_pages(self, items: list[dict]) -> None:
        if not items:
            return
        with ThreadPoolExecutor(max_workers=min(4, len(items)), thread_name_prefix="drjavan-web") as pool:
            futures = {pool.submit(_fetch_page, item.get("link") or "", self.timeout_seconds): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    page = future.result()
                except Exception:
                    continue
                item.update(page)

    def _to_evidence(self, item: dict, request: RetrievalRequest, *, rank: int) -> EvidenceItem | None:
        link = str(item.get("link") or "")
        domain = urlparse(link).netloc.casefold().removeprefix("www.")
        if not domain:
            return None
        now = datetime.now(timezone.utc)
        title = " ".join(str(item.get("page_title") or item.get("title") or "").split())
        snippet = " ".join(str(item.get("description") or "").split())
        page_text = " ".join(str(item.get("page_text") or "").split())[:6500]
        monetary_text = " ".join(str(item.get("page_monetary_text") or "").split())[:4500]
        text = "\n".join(value for value in (snippet, page_text, monetary_text) if value)
        if not (title or text):
            return None
        timestamp, date_origin = _best_date(item, text, now)
        searchable_text = " ".join((title, snippet, page_text, monetary_text))
        current_signal = _has_current_signal(searchable_text, now.year)
        geography_signal = _geography_signal(searchable_text, request)
        trust = _domain_trust(domain)
        numeric = bool(re.search(r"(?<!\w)[0-9۰-۹٠-٩][0-9۰-۹٠-٩٬,\.]*", text))
        monetary = _has_monetary_signal(text)
        score = 0.48 + min(0.16, max(0, 10 - rank) * 0.012) + (0.14 if current_signal else 0) + (0.10 if geography_signal else 0) + (0.08 if monetary else 0)
        score = min(score, 1.0)
        freshness = FreshnessClass.CURRENT if timestamp and (_age_days(timestamp, now) <= 180 or current_signal) else FreshnessClass.UNSPECIFIED
        return EvidenceItem(
            evidence_id=f"web:{_stable_id(link)}", source_type=self.source_type,
            source_name=domain, source_ref=link, text=text, title=title or None,
            context=None, timestamp=timestamp, author_or_org=domain, retrieval_score=score,
            semantic_score=None, freshness=freshness,
            metadata={"url": link, "domain": domain, "retrieved_at": now.isoformat(), "date_origin": date_origin,
                      "current_year_signal": current_signal, "geography_signal": geography_signal,
                      "query": item.get("query"), "numeric_signal": numeric, "monetary_signal": monetary},
            citation_capability="url_snippet_page", trust_tier="current_web",
            url=link, publication_year=_year_from_timestamp(timestamp), publication_type="web_page",
            retrieved_at=now.isoformat(), trust_score=trust, methodological_strength=None,
            independence_key=f"domain:{domain}|url:{_canonical_url(link)}",
        )


class OfficialInformationProvider(CurrentInformationProvider):
    source_type = SourceType.OFFICIAL

    def _query_texts(self, request: RetrievalRequest) -> tuple[str, ...]:
        base = tuple(query.text for query in request.queries if query.text.strip())
        domains = _official_domains(request.geography.country_code)
        out: list[str] = []
        for query in base[:3]:
            for domain in domains[:4]:
                out.append(f"{query} site:{domain}")
        return tuple(out or base)

    def _admit_url(self, url: str, request: RetrievalRequest) -> bool:
        if not super()._admit_url(url, request):
            return False
        domain = urlparse(url).netloc.casefold().removeprefix("www.")
        return any(domain == allowed or domain.endswith("." + allowed) for allowed in _official_domains(request.geography.country_code))

    def _to_evidence(self, item: dict, request: RetrievalRequest, *, rank: int) -> EvidenceItem | None:
        evidence = super()._to_evidence(item, request, rank=rank)
        if evidence is None:
            return None
        from dataclasses import replace
        return replace(evidence, source_type=SourceType.OFFICIAL, trust_tier="official_authority", trust_score=0.98,
                       evidence_id=evidence.evidence_id.replace("web:", "official:", 1), publication_type="official_web")



def _public_http_url(url: str, *, resolve: bool = False) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.strip().casefold().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = []
            if resolve:
                try:
                    addresses = list({ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)})
                except (OSError, ValueError):
                    return False
        for address in addresses:
            if any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved,
                    address.is_unspecified, address.is_multicast)):
                return False
        return True
    except Exception:
        return False

def _official_domains(country_code: str | None) -> tuple[str, ...]:
    if str(country_code or "").upper() == "IR":
        return ("behdasht.gov.ir", "irimc.org", "irimc.ir", "gov.ir", "fda.gov.ir", "salamat.gov.ir")
    return ("who.int", "nih.gov", "cdc.gov", "ada.org", "fdiworlddental.org")


def _fetch_page(url: str, timeout: float) -> dict:
    if not _public_http_url(url, resolve=True):
        return {}
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    last_error = None
    for attempt in range(2):
        try:
            with urlopen(req, timeout=timeout) as response:
                content_type = (response.headers.get("content-type") or "").casefold()
                if "html" not in content_type:
                    return {}
                raw = response.read(_MAX_PAGE_BYTES)
                charset = response.headers.get_content_charset() or "utf-8"
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.15)
                continue
            raise last_error
    html = raw.decode(charset, errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    full_text = " ".join(soup.get_text(" ", strip=True).split())
    text = full_text[:9000]
    monetary_text = _extract_monetary_windows(full_text)
    date = ""
    for attrs in (
        {"property": "article:published_time"}, {"name": "date"}, {"name": "pubdate"},
        {"itemprop": "datePublished"},
    ):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            date = str(node.get("content")); break
    if not date:
        node = soup.find("time")
        if node:
            date = str(node.get("datetime") or node.get_text(" ", strip=True) or "")
    return {"page_title": title, "page_text": text, "page_monetary_text": monetary_text, "page_date": date}


def _best_date(item: dict, text: str, now: datetime) -> tuple[str | None, str]:
    for key in ("page_date", "pub_date"):
        value = str(item.get(key) or "").strip()
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed.isoformat(), key
    if _has_current_signal(text, now.year):
        return datetime(now.year, 1, 1, tzinfo=timezone.utc).isoformat(), "current_year_in_content"
    return None, "missing"


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if match:
        try: return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
        except ValueError: return None
    return None


def _has_current_signal(text: str, year: int) -> bool:
    fa = str(year).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    gregorian = str(year)
    # Persian solar 1405 maps to most of Gregorian 2026 and is common in Iranian current sources.
    solar = "۱۴۰۵" if year == 2026 else ""
    lowered = text.casefold()
    return any(marker and marker in lowered for marker in (gregorian, fa, solar, "امسال", "today", "current", "latest", "به روز", "به‌روز"))


def _geography_signal(text: str, request: RetrievalRequest) -> bool:
    country = str(request.geography.country_code or "").upper()
    lowered = text.casefold()
    if country == "IR":
        return any(value in lowered for value in ("ایران", "iran", "تهران", "tehran", ".ir"))
    label = str(request.geography.label or "").casefold().strip()
    return bool(label and label in lowered)


def _domain_trust(domain: str) -> float:
    if domain.endswith(".gov.ir") or domain.endswith(".gov") or domain in {"who.int", "nih.gov", "ada.org", "irimc.org"}:
        return 0.96
    if domain == "dentkar.com" or domain.endswith(".dentkar.com"):
        return 0.82
    if any(token in domain for token in ("jobvision", "jobinja", "salary", "iranestekhdam", "medjobs", "irantalent")):
        return 0.78
    if domain.endswith(".edu") or ".ac." in domain:
        return 0.82
    return 0.58


def _age_days(timestamp: str, now: datetime) -> int:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, (now - parsed.astimezone(timezone.utc)).days)
    except Exception:
        return 99999


def _year_from_timestamp(value: str | None) -> int | None:
    if not value: return None
    match = re.match(r"(20\d{2})", value)
    return int(match.group(1)) if match else None


def _canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme.casefold()}://{p.netloc.casefold()}{p.path.rstrip('/')}"
    except Exception:
        return ""


def _stable_id(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _xml_text(node: ET.Element | None) -> str:
    return "" if node is None else " ".join("".join(node.itertext()).split())


def _strip_html(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def _trusted_seed_urls(request: RetrievalRequest) -> tuple[str, ...]:
    facets = set(request.facets)
    if str(request.geography.country_code or "").upper() != "IR":
        return ()
    solar = _solar_year(datetime.now(timezone.utc))
    urls: list[str] = []
    if facets & {"salary", "career"}:
        slug = quote(f"درآمد-دندانپزشک-عمومی-{solar}", safe="-")
        urls.append(f"https://dentkar.com/articles/{slug}")
    return tuple(urls)


def _solar_year(now: datetime) -> int:
    return now.year - (621 if (now.month, now.day) >= (3, 21) else 622)


def _admit_current_evidence(item: EvidenceItem, request: RetrievalRequest) -> bool:
    text = normalize_current_text(" ".join(value for value in (item.title, item.text) if value))
    facets = set(request.facets)
    country = str(request.geography.country_code or "").upper()
    if float(item.trust_score or 0) < 0.62:
        return False
    if country == "IR" and not bool(item.metadata.get("geography_signal")):
        return False
    if facets & {"salary", "career"}:
        body = normalize_current_text(item.text)
        dental = any(term in body for term in ("دندانپزشک", "دندان پزشکی", "dentist", "dentistry"))
        compensation = any(term in text for term in ("حقوق", "درآمد", "دستمزد", "تومان", "ریال", "salary", "income", "compensation", "درصد"))
        if not dental or not compensation:
            return False
        if "salary" in facets and not bool(item.metadata.get("monetary_signal")):
            return False
    if "cost" in facets:
        if not bool(item.metadata.get("monetary_signal")) or not any(term in text for term in ("قیمت", "هزینه", "تومان", "ریال", "price", "cost")):
            return False
    if facets & {"salary", "cost", "career", "regulation"}:
        if not (bool(item.metadata.get("current_year_signal")) or item.timestamp):
            return False
    return True



_MONEY_PATTERN = re.compile(
    r"(?<![\w\u0600-\u06ff])"
    r"[0-9۰-۹٠-٩][0-9۰-۹٠-٩٬,\.]*"
    r"(?:\s*(?:تا|الی|–|—|-|to)\s*[0-9۰-۹٠-٩][0-9۰-۹٠-٩٬,\.]*)?"
    r"\s*(?:میلیون|میلیارد|هزار|million|billion|thousand)?\s*"
    r"(?:تومان|ریال|toman|rial|irr)"
    r"(?![\w\u0600-\u06ff])",
    re.IGNORECASE,
)


def _has_monetary_signal(text: str) -> bool:
    return _MONEY_PATTERN.search(str(text or "")) is not None


def _extract_monetary_windows(text: str, *, radius: int = 260, max_chars: int = 5200) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    windows: list[str] = []
    seen: set[str] = set()
    total = 0
    for match in _MONEY_PATTERN.finditer(raw):
        chunk = raw[max(0, match.start() - radius): min(len(raw), match.end() + radius)].strip()
        key = normalize_current_text(chunk)
        if not key or key in seen:
            continue
        seen.add(key)
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunk = chunk[:remaining]
        windows.append(chunk)
        total += len(chunk) + 1
    return "\n".join(windows)

def normalize_current_text(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("‌", " ").split())


__all__ = ["CurrentInformationProvider", "OfficialInformationProvider"]
