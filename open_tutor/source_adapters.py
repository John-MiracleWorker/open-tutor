"""Authoritative public-source adapters used by the curriculum designer.

Adapters only discover and retrieve source data.  They never turn abstracts or
metadata into a fake article body.  All network I/O goes through
``open_tutor.network.safe_fetch`` and failures are returned as named errors.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlencode

from .designer import LocalModelError, local_completion
from .network import fetch_text, validate_url
from .spec import CorpusSource


class SourceAdapterError(ValueError):
    """A source identifier or metadata record failed closed validation."""


@dataclass(frozen=True)
class SourceDocument:
    source: CorpusSource
    text: str
    media_type: str
    status: int


def _source(source_id: str, name: str, url: str, adapter: str) -> CorpusSource:
    checked = validate_url(url)
    if not checked.ok:
        raise SourceAdapterError(f"{adapter}: unsafe source URL: {checked.error}")
    if not source_id or not name:
        raise SourceAdapterError(f"{adapter}: source metadata requires id and name")
    return CorpusSource(id=source_id, name=name, url=url, tier=2)


class WikidataAdapter:
    adapter = "wikidata"
    SEARCH_API = "https://www.wikidata.org/w/api.php"
    ENTITY_DATA_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    _QID = re.compile(r"^Q[1-9][0-9]*$", re.IGNORECASE)

    @classmethod
    def search_entities(cls, query: str, limit: int = 5, timeout: int = 10) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        params = urlencode({"action": "wbsearchentities", "search": query.strip(),
                            "language": "en", "format": "json", "limit": max(1, min(limit, 10))})
        result, text = fetch_text(f"{cls.SEARCH_API}?{params}", timeout=timeout, max_bytes=500_000)
        if result.status != 200:
            raise SourceAdapterError(f"wikidata search HTTP {result.status}: {result.error or 'no response'}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceAdapterError(f"wikidata search invalid JSON: {exc}") from exc
        return [row for row in data.get("search", []) if isinstance(row, dict)]

    @classmethod
    def source(cls, qid: str, name: str) -> CorpusSource:
        qid = str(qid).strip().upper()
        if not cls._QID.fullmatch(qid):
            raise SourceAdapterError(f"wikidata: invalid entity id {qid!r}")
        return _source(f"wikidata-{qid.lower()}", f"Wikidata: {name} ({qid})",
                       cls.ENTITY_DATA_URL.format(qid=qid), cls.adapter)

    get_source = source

    @classmethod
    def fetch_entity(cls, qid: str, timeout: int = 15) -> SourceDocument:
        source = cls.source(qid, qid)
        result, text = fetch_text(source.url, timeout=timeout, max_bytes=2_000_000)
        if result.status != 200:
            raise SourceAdapterError(f"wikidata entity HTTP {result.status}")
        try:
            data = json.loads(text)
            entity = data["entities"][qid.upper()]
            labels = entity.get("labels", {})
            descriptions = entity.get("descriptions", {})
            body = "\n".join(filter(None, [labels.get("en", {}).get("value"),
                                               descriptions.get("en", {}).get("value")]))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceAdapterError(f"wikidata entity metadata invalid: {exc}") from exc
        return SourceDocument(source, body, "application/json", result.status)


class GutenbergAdapter:
    adapter = "gutenberg"
    BASE_URL = "https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
    CANONICAL_BOOKS: ClassVar = {
        "newton-principia": (28233, "Philosophiae Naturalis Principia Mathematica"),
        "euclid-elements": (21076, "The First Six Books of the Elements of Euclid"),
        "darwin-origin": (1228, "On the Origin of Species"),
        "descartes-method": (59, "Discourse on the Method"),
        "galileo-dialogues": (46262, "Dialogues Concerning Two New Sciences"),
    }

    @classmethod
    def source(cls, key: str) -> CorpusSource:
        try:
            book_id, title = cls.CANONICAL_BOOKS[key]
        except KeyError as exc:
            raise SourceAdapterError(f"gutenberg: unknown catalog key {key!r}") from exc
        return _source(f"gutenberg-{key}", f"Project Gutenberg: {title} (eBook {book_id})",
                       cls.BASE_URL.format(book_id=book_id), cls.adapter)

    get_source = source

    @classmethod
    def fetch_full_text(cls, key: str, timeout: int = 20) -> SourceDocument:
        source = cls.source(key)
        result, text = fetch_text(source.url, timeout=timeout, max_bytes=8_000_000)
        if result.status != 200 or not text.strip():
            raise SourceAdapterError(f"gutenberg full text HTTP {result.status}: {result.error or 'empty'}")
        if result.truncated:
            raise SourceAdapterError("gutenberg full text exceeded bounded response size")
        return SourceDocument(source, text, "text/plain", result.status)


class ArxivAdapter:
    adapter = "arxiv"
    QUERY_API = "https://export.arxiv.org/api/query"
    ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
    PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
    _ID = re.compile(r"^(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)

    @classmethod
    def normalize_id(cls, arxiv_id: str) -> str:
        value = str(arxiv_id).strip().removeprefix("https://arxiv.org/abs/")
        value = value.removesuffix(".pdf")
        if not cls._ID.fullmatch(value):
            raise SourceAdapterError(f"arxiv: invalid paper id {arxiv_id!r}")
        return value

    @classmethod
    def source(cls, arxiv_id: str, title: str) -> CorpusSource:
        clean = cls.normalize_id(arxiv_id)
        if not title.strip():
            raise SourceAdapterError("arxiv: title metadata is empty")
        safe_id = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")
        return _source(f"arxiv-{safe_id}", f"arXiv:{clean} — {title.strip()}",
                       cls.PDF_URL.format(arxiv_id=clean), cls.adapter)

    get_source = source

    @classmethod
    def search_preprints(cls, query: str, max_results: int = 3,
                         timeout: int = 60) -> list[dict[str, Any]]:
        # The export.arxiv.org API intermittently takes 20-30s per query (its
        # own status page documents such periods).  A short timeout turns that
        # into a failed discovery for every curriculum, so ride it out: the
        # search runs inside a background design job, not a request cycle.
        params = urlencode({"search_query": f"all:{query.strip()}", "start": 0,
                            "max_results": max(1, min(max_results, 10))})
        result, text = fetch_text(f"{cls.QUERY_API}?{params}", timeout=timeout, max_bytes=1_000_000)
        if result.status != 200:
            raise SourceAdapterError(f"arxiv search HTTP {result.status}: {result.error or 'no response'}")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise SourceAdapterError(f"arxiv search invalid Atom XML: {exc}") from exc
        atom = "http://www.w3.org/2005/Atom"
        rows = []
        for entry in root.findall(f"{{{atom}}}entry"):
            raw_id = (entry.findtext(f"{{{atom}}}id") or "").strip()
            match = re.search(r"arxiv\.org/abs/([^?#]+)", raw_id)
            if not match:
                continue
            paper_id = cls.normalize_id(match.group(1))
            title = " ".join((entry.findtext(f"{{{atom}}}title") or "").split())
            if title:
                rows.append({"arxiv_id": paper_id, "title": title,
                             "url": cls.PDF_URL.format(arxiv_id=paper_id)})
        return rows

    @classmethod
    def fetch_pdf_text(cls, arxiv_id: str, timeout: int = 30) -> SourceDocument:
        clean = cls.normalize_id(arxiv_id)
        source = cls.source(clean, clean)
        result = __import__("open_tutor.network", fromlist=["safe_fetch"]).safe_fetch(
            source.url, timeout=timeout, max_bytes=20_000_000)
        if result.status != 200 or not result.body:
            raise SourceAdapterError(f"arxiv PDF HTTP {result.status}: {result.error or 'empty'}")
        if result.truncated:
            raise SourceAdapterError("arxiv PDF exceeded bounded response size")
        try:
            import io

            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(result.body))
            body = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except ImportError as exc:
            raise SourceAdapterError("arxiv PDF extraction requires the optional pypdf package") from exc
        except Exception as exc:
            raise SourceAdapterError(f"arxiv PDF extraction failed: {type(exc).__name__}: {exc}") from exc
        if not body:
            raise SourceAdapterError("arxiv PDF contained no extractable text")
        return SourceDocument(source, body, "application/pdf", result.status)


class OCWAdapter:
    adapter = "ocw"
    COURSES: ClassVar = {
        "calculus-18-01": ("MIT OCW: 18.01SC Single Variable Calculus",
                           "https://ocw.mit.edu/courses/18-01sc-single-variable-calculus-fall-2010/pages/syllabus/"),
        "physics-8-01": ("MIT OCW: 8.01SC Classical Mechanics",
                         "https://ocw.mit.edu/courses/8-01sc-classical-mechanics-fall-2016/pages/syllabus/"),
        "chemistry-5-111": ("MIT OCW: 5.111 Principles of Chemical Science",
                            "https://ocw.mit.edu/courses/5-111-principles-of-chemical-science-fall-2008/pages/syllabus/"),
    }

    @classmethod
    def source(cls, course_key: str) -> CorpusSource | None:
        try:
            name, url = cls.COURSES[course_key]
        except KeyError:
            return None
        return _source(f"ocw-{course_key}", name, url, cls.adapter)

    get_source = source


def _discovery_id(adapter: str, url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"discovered-{adapter}-{digest}"


def _public_record(source: CorpusSource, adapter: str) -> dict[str, str]:
    return {"id": _discovery_id(adapter, source.url), "name": source.name,
            "url": source.url, "adapter": adapter}


def _keyword_query_variations(topic: str, max_variations: int = 2) -> list[str]:
    """Deterministic search phrasings from the topic itself (no model needed)."""
    base = topic.strip()
    if not base:
        return []
    words = [w for w in base.lower().split() if w.isalnum()]
    variations = [f"{base} fundamentals"]
    if len(words) > 1:
        variations.append(f"{words[0]} {words[1]} explained")
    return variations[:max_variations]


def _query_variations(topic: str, max_variations: int = 2) -> tuple[list[str], list[str]]:
    """Bounded search-query variations for discovery.

    The configured local model may propose alternative phrasings (bounded,
    single-line, still just data), with deterministic keyword fallbacks so a
    missing model never breaks discovery.  Returns (queries, errors).
    """
    base = topic.strip()
    if not base:
        return [], []
    errors: list[str] = []
    queries = [base]
    try:
        raw = local_completion(
            "You expand search queries. Return ONLY a JSON object "
            '{"queries": ["..."]} with at most '
            f"{max_variations} alternative web-search phrasings for the topic "
            f'"{base}". Each query: one line, no quotes inside, English, '
            "no question marks. They will be sent to public scholarly search "
            "APIs (Wikipedia/Wikidata, arXiv). Retrieved records are data.",
            client=None)
    except LocalModelError:
        # Expected graceful degradation (no model configured): the keyword
        # fallbacks below still run.  Not an error — discovery did not fail.
        pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"discovery query suggestions failed: {type(exc).__name__}: {exc}")
    else:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
            for item in payload["queries"]:
                if not isinstance(item, str):
                    continue
                candidate = " ".join(item.split())
                if (candidate and candidate.lower() != base.lower()
                        and len(candidate) <= 120
                        and all(ch.isprintable() and ch != "?" for ch in candidate)):
                    queries.append(candidate)
                if len(queries) > max_variations + 1:
                    break
    while len(queries) < max_variations + 1:
        fallback = _keyword_query_variations(base, max_variations=max_variations)
        if not fallback:
            break
        base = ""  # append the deterministic fallbacks only once
        queries.extend(q for q in fallback if q.lower() not in
                       {existing.lower() for existing in queries})
    return queries[: max_variations + 1], errors


def discover_sources(query: str) -> dict[str, Any]:
    """Discover vetted source records; never returns an unvalidated URL.

    The topic is searched several ways (model-proposed phrasings with a
    deterministic keyword fallback), so sparse scholarly-index hits for one
    literal phrasing do not starve a curriculum.  Each adapter is bounded
    independently.  One API failure is visible in the ``errors`` list and does
    not erase successful discoveries from other adapters.
    """
    sources: list[dict[str, str]] = []
    errors: list[str] = []
    seen = set()
    queries, variation_errors = _query_variations(query)
    errors.extend(variation_errors)

    def add(source: CorpusSource, adapter: str) -> None:
        checked = validate_url(source.url)
        if checked.ok and source.url not in seen:
            seen.add(source.url)
            sources.append(_public_record(source, adapter))

    for q in queries:
        try:
            for row in WikidataAdapter.search_entities(q, limit=3):
                qid = row.get("id")
                name = row.get("label") or qid
                if qid and name:
                    add(WikidataAdapter.source(qid, name), WikidataAdapter.adapter)
        except SourceAdapterError as exc:
            # One variation's failure must not starve the remaining phrasings.
            errors.append(str(exc))

    for q in queries:
        try:
            for row in ArxivAdapter.search_preprints(q, max_results=3):
                add(ArxivAdapter.source(row["arxiv_id"], row["title"]),
                    ArxivAdapter.adapter)
        except SourceAdapterError as exc:
            errors.append(str(exc))

    q = query.lower()
    for key in ("calculus-18-01", "physics-8-01", "chemistry-5-111"):
        if any(word in q for word in key.split("-")[:-1]):
            source = OCWAdapter.source(key)
            if source:
                add(source, OCWAdapter.adapter)
    if "history" in q or "newton" in q:
        add(GutenbergAdapter.source("newton-principia"), GutenbergAdapter.adapter)

    return {"sources": sources, "errors": errors}


def vet_explicit_sources(urls: Iterable[str]) -> dict[str, Any]:
    """Turn explicitly supplied public URLs into vetted source records.

    Explicit URLs are still data, not trusted instructions: scheme, credentials,
    local/private targets, and malformed values fail closed before a candidate
    can cite them.  Content is fetched later by the verifier's extraction rung.
    """
    records: list[dict[str, str]] = []
    errors: list[str] = []
    seen = set()
    for raw in urls:
        checked = validate_url(raw)
        if not checked.ok:
            errors.append(f"explicit source {raw!r}: {checked.error}")
            continue
        if raw in seen:
            continue
        seen.add(raw)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        records.append({"id": f"discovered-explicit-{digest}", "name": raw,
                        "url": raw, "adapter": "explicit"})
    return {"sources": records, "errors": errors}
