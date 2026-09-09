from __future__ import annotations

from datetime import datetime, timezone
import re
import time
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .models import EvidenceItem, FreshnessClass, RetrievalRequest, RetrievalResult, SourceType

_NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_USER_AGENT = "DrJavanBot/0.7 (+https://github.com/ArianGhsm/DrJavanGroupDatabase)"
_NCBI_RATE_LOCK = threading.Lock()
_NCBI_LAST_REQUEST = 0.0
_NCBI_MIN_INTERVAL_SECONDS = 0.36


class PubMedScientificProvider:
    source_type = SourceType.SCIENTIFIC

    def __init__(self, *, timeout_seconds: float = 7.0, max_queries: int = 4) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_queries = max(1, int(max_queries))

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.source_type != SourceType.SCIENTIFIC:
            raise ValueError("scientific provider received non-scientific request")
        started = time.perf_counter()
        pmids: list[str] = []
        query_hits: dict[str, int] = {}
        query_count = 0
        try:
            for query in request.queries[: self.max_queries]:
                term = _scientific_term(query.text, request.facets)
                if not term:
                    continue
                query_count += 1
                ids = self._search(term, retmax=max(request.top_k, 12))
                for pmid in ids:
                    query_hits[pmid] = query_hits.get(pmid, 0) + 1
                    if pmid not in pmids:
                        pmids.append(pmid)
                if len(pmids) >= max(request.top_k * 2, 20):
                    break
            articles = self._fetch(pmids[: max(request.top_k * 2, 20)]) if pmids else ()
            items = tuple(
                _article_to_evidence(article, rank=index + 1, query_hits=query_hits.get(article["pmid"], 0))
                for index, article in enumerate(articles)
            )
            items = tuple(sorted(items, key=_scientific_sort_key, reverse=True)[: request.top_k])
            return RetrievalResult(
                source_type=SourceType.SCIENTIFIC,
                items=items,
                query_count=query_count,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            return RetrievalResult(
                source_type=SourceType.SCIENTIFIC,
                items=(),
                query_count=query_count,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                unavailable_reason=f"pubmed_{type(exc).__name__.casefold()}",
            )

    def _search(self, term: str, *, retmax: int) -> tuple[str, ...]:
        url = _NCBI + "/esearch.fcgi?" + urlencode({
            "db": "pubmed", "term": term, "retmode": "json", "retmax": min(max(retmax, 1), 50),
            "sort": "relevance",
        })
        payload = _http_text(url, self.timeout_seconds)
        import json
        data = json.loads(payload)
        ids = data.get("esearchresult", {}).get("idlist", [])
        return tuple(str(value) for value in ids if str(value).isdigit())

    def _fetch(self, pmids: list[str]) -> tuple[dict, ...]:
        if not pmids:
            return ()
        url = _NCBI + "/efetch.fcgi?" + urlencode({
            "db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
        })
        root = ET.fromstring(_http_text(url, self.timeout_seconds))
        out: list[dict] = []
        for article in root.findall(".//PubmedArticle"):
            parsed = _parse_pubmed_article(article)
            if parsed.get("pmid") and parsed.get("title"):
                out.append(parsed)
        order = {pmid: index for index, pmid in enumerate(pmids)}
        out.sort(key=lambda item: order.get(item["pmid"], 10_000))
        return tuple(out)


def _http_text(url: str, timeout: float) -> str:
    global _NCBI_LAST_REQUEST
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json, application/xml, text/xml"})
    last_error: Exception | None = None
    for attempt in range(2):
        with _NCBI_RATE_LOCK:
            wait = _NCBI_MIN_INTERVAL_SECONDS - (time.monotonic() - _NCBI_LAST_REQUEST)
            if wait > 0:
                time.sleep(wait)
            _NCBI_LAST_REQUEST = time.monotonic()
        try:
            with urlopen(req, timeout=timeout) as response:
                body = response.read(3_000_000)
            return body.decode("utf-8", errors="replace")
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt >= 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                pause = min(2.0, max(0.4, float(retry_after))) if retry_after else 0.6
            except (TypeError, ValueError):
                pause = 0.6
            time.sleep(pause)
        except (URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= 1:
                raise
            time.sleep(0.5)
    raise RuntimeError("NCBI request failed") from last_error


def _scientific_term(text: str, facets: tuple[str, ...]) -> str:
    term = " ".join(str(text).split())
    if not term:
        return ""
    facet_set = set(facets)
    if facet_set & {"prevalence", "frequency", "epidemiology"} and "systematic review" not in term.casefold():
        return f"({term}) AND (prevalence OR frequency OR epidemiology)"
    if "guideline" in facet_set:
        return f"({term}) AND (guideline OR consensus OR practice guideline)"
    if facet_set & {"treatment", "clinical_decision", "comparison"}:
        return f"({term}) AND (systematic review OR meta-analysis OR guideline OR randomized)"
    return term


def _parse_pubmed_article(node: ET.Element) -> dict:
    citation = node.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    pmid = _node_text(citation.find("PMID")) if citation is not None else ""
    title = _node_text(article.find("ArticleTitle")) if article is not None else ""
    abstract_parts: list[str] = []
    if article is not None:
        for item in article.findall("Abstract/AbstractText"):
            text = _node_text(item)
            label = (item.attrib.get("Label") or "").strip()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = " ".join(abstract_parts)
    authors: list[str] = []
    if article is not None:
        for author in article.findall("AuthorList/Author")[:12]:
            collective = _node_text(author.find("CollectiveName"))
            if collective:
                authors.append(collective); continue
            last = _node_text(author.find("LastName"))
            fore = _node_text(author.find("ForeName"))
            name = " ".join(x for x in (fore, last) if x)
            if name:
                authors.append(name)
    journal = _node_text(article.find("Journal/Title")) if article is not None else ""
    year = _publication_year(article)
    pub_types = tuple(
        _node_text(item) for item in (article.findall("PublicationTypeList/PublicationType") if article is not None else [])
        if _node_text(item)
    )
    doi = ""
    for aid in node.findall("PubmedData/ArticleIdList/ArticleId"):
        if (aid.attrib.get("IdType") or "").casefold() == "doi":
            doi = _node_text(aid); break
    return {
        "pmid": pmid, "title": title, "abstract": abstract, "authors": tuple(authors),
        "journal": journal, "year": year, "publication_types": pub_types, "doi": doi,
    }


def _publication_year(article: ET.Element | None) -> int | None:
    if article is None:
        return None
    for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
        raw = _node_text(article.find(path))
        if raw.isdigit() and len(raw) == 4:
            return int(raw)
    medline = _node_text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))
    match = re.search(r"\b(19|20)\d{2}\b", medline)
    return int(match.group(0)) if match else None


def _node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _article_to_evidence(article: dict, *, rank: int, query_hits: int) -> EvidenceItem:
    now = datetime.now(timezone.utc)
    year = article.get("year")
    pub_types = tuple(article.get("publication_types") or ())
    trust_tier, strength = _publication_strength(pub_types)
    pmid = str(article["pmid"])
    doi = str(article.get("doi") or "") or None
    text = str(article.get("abstract") or "")
    title = str(article.get("title") or "")
    author = ", ".join(article.get("authors") or ())[:500] or str(article.get("journal") or "PubMed")
    timestamp = f"{int(year):04d}-01-01T00:00:00+00:00" if year else None
    freshness = FreshnessClass.RECENT if year and int(year) >= now.year - 5 else FreshnessClass.EVERGREEN
    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    score = min(1.0, 0.68 + min(query_hits, 3) * 0.08 + max(0, 8 - rank) * 0.01)
    return EvidenceItem(
        evidence_id=f"pubmed:{pmid}", source_type=SourceType.SCIENTIFIC,
        source_name=str(article.get("journal") or "PubMed"), source_ref=f"PMID:{pmid}",
        text=text, title=title, context=None, timestamp=timestamp, author_or_org=author,
        retrieval_score=score, semantic_score=None, freshness=freshness,
        metadata={"pmid": pmid, "doi": doi, "journal": article.get("journal"), "publication_types": pub_types,
                  "query_hits": query_hits, "retrieved_at": now.isoformat(), "url": url},
        citation_capability="pubmed_abstract_metadata", trust_tier=trust_tier,
        url=url, publication_year=year, publication_type=(pub_types[0] if pub_types else None),
        retrieved_at=now.isoformat(), trust_score=strength, methodological_strength=strength,
        independence_key=(f"doi:{doi}" if doi else f"pmid:{pmid}"),
    )


def _publication_strength(pub_types: tuple[str, ...]) -> tuple[str, float]:
    joined = " ".join(pub_types).casefold()
    if "practice guideline" in joined or "guideline" in joined:
        return "clinical_guideline", 1.0
    if "systematic review" in joined or "meta-analysis" in joined:
        return "systematic_review", 0.97
    if "review" in joined:
        return "peer_reviewed_review", 0.90
    if "randomized controlled trial" in joined:
        return "randomized_trial", 0.90
    return "peer_reviewed_primary", 0.78


def _scientific_sort_key(item: EvidenceItem) -> tuple[float, float, int]:
    return (float(item.trust_score or 0), float(item.retrieval_score or 0), int(item.publication_year or 0))


__all__ = ["PubMedScientificProvider"]
