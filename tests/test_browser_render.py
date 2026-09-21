"""P0.5 — SPA browser-render fallback.

Covers the three behaviors the card requires:
  1. pluggable backend interface  (injection via `browser_render(backend=...)`
     and `fetch_live(render_backend=...)`, plus `set_render_backend`)
  2. no-op / no-browser path      (honestly stays `needs-render`, never crashes)
  3. promotion path               (rendered body >= MIN_GROUNDING_CHARS AND
     covering the node  ->  source is `live` and the node is grounded)

All tests are offline: the static rungs of the ladder are stubbed, and the
render backend is a fake callable — no network, no real browser, no LLM.
"""
from __future__ import annotations

import pytest

from open_tutor import scraper, verifier
from open_tutor.spec import CorpusSource, Node

THICK_BODY = ("A qubit is the quantum analogue of the classical bit. It is a "
              "two-level quantum system, usually denoted |0> and |1>. "
              "Superposition lets a qubit exist in a linear combination of both "
              "basis states at once, with complex amplitudes whose squared "
              "magnitudes give measurement probabilities under the Born rule. "
              "Quantum gates are unitary operations on these amplitudes, and "
              "entanglement is the non-separable correlation between two or "
              "more qubits that no classical state can reproduce. Grover's "
              "algorithm uses amplitude amplification to search an unsorted "
              "database of N entries in O(sqrt(N)) queries. All of this is the "
              "core vocabulary of quantum computing. ") * 6  # ~2700 chars, well over the bar


def _rendered_html(body: str) -> str:
    return (
        "<html><head><title>Quantum Computing — Rendered Module</title></head>"
        f"<body><article><h1>Quantum Computing</h1><p>{body}</p></article>"
        "</body></html>"
    )


def _thin_extracted(url: str = "https://spa.example.com/lesson") -> scraper.Extracted:
    """What ladder rungs 1-2 honestly produce for an SPA shell."""
    return scraper.Extracted(
        url=url, ok=False, method="none", http_status=200,
        body="Loading quantum module…", body_len=25, needs_render=True,
        error="thin body; SPA suspected → needs browser render",
    )


@pytest.fixture(autouse=True)
def _clean_backend():
    scraper.set_render_backend(None)
    yield
    scraper.set_render_backend(None)


# ---------- 1. pluggable backend interface ----------

def test_backend_injection_is_honored():
    calls = []

    def fake(url: str):
        calls.append(url)
        return _rendered_html(THICK_BODY)

    ex = scraper.browser_render("https://spa.example.com/lesson", backend=fake)
    assert calls == ["https://spa.example.com/lesson"]
    assert ex.method == "browser"
    assert ex.ok and ex.body_len >= 2000
    # exact Extracted shape preserved
    assert set(ex.__dataclass_fields__) == {
        "url", "ok", "method", "http_status", "title", "author", "date",
        "body", "body_len", "needs_render", "error",
    }


def test_set_render_backend_process_wide():
    def fake(url: str):
        return _rendered_html(THICK_BODY)

    scraper.set_render_backend(fake)
    try:
        ex = scraper.browser_render("https://spa.example.com/x")  # no explicit backend
        assert ex.ok and ex.method == "browser"
    finally:
        scraper.set_render_backend(None)


# ---------- 2. no-op / no-browser honesty ----------

def test_no_backend_stays_needs_render():
    ex = scraper.browser_render("https://spa.example.com/lesson",
                                backend=scraper._noop_render_backend)
    assert ex.ok is False
    assert ex.needs_render is True
    assert ex.body_len == 0
    assert ex.error  # failure is first-class, never silent


def test_fetch_live_without_browser_is_honest_and_safe(monkeypatch):
    """No browser anywhere: fetch_live must not crash and must report needs-render."""
    monkeypatch.setenv("OPEN_TUTOR_RENDER_BACKEND", "off")
    scraper.set_render_backend(scraper._noop_render_backend)
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))

    res = verifier.fetch_live("https://spa.example.com/lesson")
    assert res["ok"] is False
    assert res["needs_render"] is True
    assert res["text_len"] == 25
    # the status word the report prints must stay the honest one
    status = "live" if res["ok"] else ("needs-render" if res["needs_render"] else "dead")
    assert status == "needs-render"


def test_rendered_but_still_thin_stays_needs_render(monkeypatch):
    """A render that yields only marketing copy must NOT promote the source."""
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))
    thin_html = _rendered_html("Welcome to Quantum Learning. Sign up today!")
    res = verifier.fetch_live("https://spa.example.com/lesson",
                              render_backend=lambda url: thin_html)
    # (fetch_live's static rung is stubbed thin, so the render rung is what ran)
    assert res["needs_render"] is True
    assert res["ok"] is False
    assert res["text_len"] < verifier.MIN_GROUNDING_CHARS


def test_bar_is_never_lowered():
    assert verifier.MIN_GROUNDING_CHARS == 2500


# ---------- 3. promotion path ----------

def test_promotion_live_and_grounded(monkeypatch):
    """Rendered body >= bar AND covering the node -> source live, node grounded."""
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))

    def fake(url: str):
        return _rendered_html(THICK_BODY)

    res = verifier.fetch_live("https://spa.example.com/lesson", render_backend=fake)
    assert res["ok"] is True
    assert res["method"] == "browser"
    assert res["text_len"] >= verifier.MIN_GROUNDING_CHARS
    status = "live" if res["ok"] else ("needs-render" if res["needs_render"] else "dead")
    assert status == "live"

    node = Node(id="qubit", title="The Qubit", defn="a two-level quantum system",
                covers_keywords=["qubit"], grounding_corpus=["src-spa"])
    corpus_text = {"src-spa": res["text"]}
    assert "src-spa" in verifier.coverage(node, corpus_text)


def test_promotion_rejected_when_body_below_bar(monkeypatch):
    """A rendered body under 2500 chars must not count as coverage."""
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))
    under = "qubit " * 100  # 800 chars, keyword present but under the bar

    res = verifier.fetch_live("https://spa.example.com/lesson",
                              render_backend=lambda url: _rendered_html(under))
    assert res["ok"] is True and res["text_len"] >= 200  # live-ish, >= 200 chars
    # but it must NOT cover a node — the bar is a floor
    node = Node(id="qubit", title="The Qubit", defn="a two-level quantum system",
                covers_keywords=["qubit"], grounding_corpus=["s"])
    assert verifier.coverage(node, {"s": res["text"]}) == []


def test_promotion_rejected_when_keyword_miss(monkeypatch):
    """Over the bar but no node keyword -> still not coverage."""
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))
    off_topic = ("classical thermodynamics is the study of heat, work and "
                 "entropy in macroscopic systems ") * 200  # > 2500 chars, no 'qubit'

    res = verifier.fetch_live("https://spa.example.com/lesson",
                              render_backend=lambda url: _rendered_html(off_topic))
    node = Node(id="qubit", title="The Qubit", defn="a two-level quantum system",
                covers_keywords=["qubit"], grounding_corpus=["s"])
    assert res["text_len"] >= verifier.MIN_GROUNDING_CHARS  # bar cleared
    assert verifier.coverage(node, {"s": res["text"]}) == []  # keyword miss


def test_full_node_becomes_grounded_via_rendered_source(monkeypatch):
    """End-to-end decision: a node whose ONLY substantive source is a rendered SPA."""
    monkeypatch.setattr(scraper, "extract", lambda url, timeout=12: _thin_extracted(url))
    monkeypatch.setattr(scraper, "browser_render",
                        lambda url, backend=None: scraper.Extracted(
                            url=url, ok=True, method="browser", http_status=200,
                            title="Quantum", body=THICK_BODY, body_len=len(THICK_BODY)))

    spec_nodes = [Node(id="qubit", title="The Qubit", defn="a two-level quantum system",
                       covers_keywords=["qubit"], grounding_corpus=["ibm-spa"])]
    corpus = [CorpusSource(id="ibm-spa", name="IBM Quantum Learning",
                           url="https://learning.quantum.ibm.com/")]

    report = _verify_with_backend(monkeypatch, corpus, spec_nodes)
    assert report["summary"]["grounded"] == 1
    assert report["summary"]["corpus_live"] == 1
    assert report["corpus"][0]["method"] == "browser"
    assert report["corpus"][0]["status"] == "live"


def _verify_with_backend(monkeypatch, corpus, nodes):
    """Run verifier.verify with the render rung stubbed, no network."""
    from open_tutor.spec import CurriculumSpec

    monkeypatch.setattr(verifier, "run_oracle", lambda name: {"ok": True, "result": {}})
    spec = CurriculumSpec(subject="test", title="t", corpus=corpus, nodes=nodes)
    return verifier.verify(spec, out_dir="/tmp/open-tutor-p05-test-out")
