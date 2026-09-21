"""RED coverage for the P4 research/designer workstream.

These tests deliberately exercise the public contracts without internet or a
model.  Live proofs are run separately and recorded in WORKSTREAM.md.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from open_tutor import network, oracles, source_adapters
from open_tutor.designer import CandidateError, LocalCompletionClient, LocalModelError, candidate_to_spec
from open_tutor.generator import (
    generate_calculus,
    generate_chemistry,
    generate_history,
    generate_music_theory,
)
from open_tutor.spec import CurriculumSpec


def test_discover_sources_uses_model_query_variations_and_dedupes(monkeypatch):
    """Discovery must search the topic several ways, not one literal string:
    the model proposes bounded query variations (data only), every variation
    goes through the same vetted adapters, and duplicate records collapse."""
    calls = {"wikidata": [], "arxiv": []}

    def fake_wikidata(query, limit=3, **kw):
        calls["wikidata"].append(query)
        if query == "quantum decoherence":
            return [{"id": "Q21207", "label": "Quantum decoherence"}]
        return []

    def fake_arxiv(query, max_results=3, **kw):
        calls["arxiv"].append(query)
        if "fundamentals" in query or query == "quantum decoherence":
            return [{"arxiv_id": "1706.03762v7", "title": "Decoherence, einselection"}]
        return []

    monkeypatch.setattr(source_adapters.WikidataAdapter, "search_entities",
                        staticmethod(fake_wikidata))
    monkeypatch.setattr(source_adapters.ArxivAdapter, "search_preprints",
                        staticmethod(fake_arxiv))
    result = source_adapters.discover_sources("quantum decoherence")

    assert calls["arxiv"][0] == "quantum decoherence"
    assert len(calls["arxiv"]) >= 3
    assert len(set(calls["arxiv"])) == len(calls["arxiv"])  # no wasted duplicates
    assert any("fundamentals" in q for q in calls["arxiv"])
    assert "quantum decoherence" in calls["wikidata"]
    arxiv_rows = [s for s in result["sources"] if "arxiv.org" in s["url"]]
    assert len(arxiv_rows) == 1  # same record from two variations collapses
    assert {s["url"] for s in result["sources"]} == {s["url"] for s in result["sources"]}
    assert len({s["id"] for s in result["sources"]}) == len(result["sources"])


def test_discovery_query_variations_fall_back_to_keywords_without_model(monkeypatch):
    """No configured model must never break discovery: fall back to
    deterministic keyword variations built from the topic itself."""
    monkeypatch.delenv("OPEN_TUTOR_MODEL", raising=False)
    seen = []

    def fake_arxiv(query, max_results=3, **kw):
        seen.append(query)
        return []

    monkeypatch.setattr(source_adapters.ArxivAdapter, "search_preprints",
                        staticmethod(fake_arxiv))
    monkeypatch.setattr(source_adapters.WikidataAdapter, "search_entities",
                        staticmethod(lambda query, limit=3, **kw: []))
    result = source_adapters.discover_sources("fermionic lattice theory")

    assert len(seen) >= 3  # original + at least two variations
    assert seen[0] == "fermionic lattice theory"
    assert seen[1] == "fermionic lattice theory fundamentals"
    assert result == {"sources": [], "errors": []}


def test_curated_nonquantum_starters_are_real_dags_and_no_t3_history():
    specs = [generate_calculus(), generate_music_theory(), generate_chemistry()]
    assert all(len(s.nodes) >= 3 for s in specs)
    assert all(all(n.oracle for n in s.nodes) for s in specs)
    assert len({s.subject for s in specs}) == 3
    assert not generate_history().oracle
    assert all(n.oracle is None for n in generate_history().nodes)
    assert all(n.status == "unknown" and not n.verification for s in specs for n in s.nodes)


def test_curated_source_ids_are_isolated_per_subject():
    specs = [generate_calculus(), generate_music_theory(), generate_chemistry()]
    ids = [{c.id for c in s.corpus} for s in specs]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
    for spec in specs:
        valid = {c.id for c in spec.corpus}
        assert all(set(n.grounding_corpus) <= valid for n in spec.nodes)


def test_candidate_schema_rejects_generated_verdict_and_unvetted_source():
    payload = {
        "subject": "calculus",
        "title": "Calculus",
        "scope": {"level": "beginner", "depth": "foundations", "assumed_prereqs": []},
        "corpus": [{"id": "discovered-1", "name": "Good", "url": "https://example.org"}],
        "nodes": [{
            "id": "limits", "title": "Limits", "def": "A limit describes nearby behavior.",
            "prereqs": [], "misconceptions": [{"id": "m1", "text": "A limit is a value reached."}],
            "grounding_corpus": ["invented"], "oracle": "sympy_derivative",
            "covers_keywords": ["limit"], "status": "grounded",
        }],
    }
    with pytest.raises(CandidateError, match="source|status"):
        candidate_to_spec(payload, vetted_sources={"discovered-1": payload["corpus"][0]})


def test_candidate_schema_accepts_only_registry_oracles_and_roundtrips():
    source = {"id": "calculus-wiki", "name": "Calculus", "url": "https://example.org/calculus"}
    payload = {
        "subject": "calculus",
        "title": "Calculus",
        "scope": {"level": "beginner", "depth": "foundations", "assumed_prereqs": []},
        "corpus": [source],
        "nodes": [{
            "id": "derivative", "title": "Derivative", "def": "A derivative is the instantaneous rate of change of a function.",
            "prereqs": [], "misconceptions": [{"id": "m1", "text": "It is only a slope."}],
            "grounding_corpus": [source["id"]], "oracle": "sympy_derivative",
            "covers_keywords": ["derivative"],
        }],
    }
    spec = candidate_to_spec(payload, vetted_sources={source["id"]: source})
    assert isinstance(spec, CurriculumSpec)
    assert not spec.nodes[0].verification and spec.nodes[0].status == "unknown"


def test_local_model_errors_are_bounded_and_explicit(monkeypatch):
    monkeypatch.delenv("OPEN_TUTOR_MODEL", raising=False)
    monkeypatch.delenv("OPEN_TUTOR_BASE_URL", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    from open_tutor.designer import LocalCompletionClient

    with pytest.raises(LocalModelError, match="OPEN_TUTOR_MODEL"):
        LocalCompletionClient.from_env()


def test_network_blocks_private_and_metadata_addresses():
    for url in (
        "http://localhost:11434/v1",
        "http://127.0.0.1:8000",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
    ):
        result = network.validate_url(url)
        assert not result.ok, (url, result)


def test_type_checker_is_real_mypy_execution(monkeypatch):
    result = oracles.python_type_checker()
    assert result["checker"] == "mypy"
    assert result["type_safe"] is True
    assert "annotation" not in json.dumps(result).lower()


def test_adapter_metadata_validation_rejects_bad_arxiv_and_gutenberg():
    with pytest.raises(source_adapters.SourceAdapterError):
        source_adapters.ArxivAdapter.source("not-an-arxiv-id", "Bad")
    with pytest.raises(source_adapters.SourceAdapterError):
        source_adapters.GutenbergAdapter.source("not-in-catalog")


def test_blocked_candidate_does_not_replace_active_bundle(tmp_path, monkeypatch):
    from open_tutor import pipeline
    from open_tutor.spec import save_yaml

    active = generate_calculus()
    active_path = tmp_path / "curriculum" / "calculus.yaml"
    active_path.parent.mkdir()
    save_yaml(active, str(active_path))
    failed = {
        "subject": "calculus", "structural": {"ok": True, "errors": {}},
        "corpus": [], "nodes": [], "oracle_outputs": {},
        "summary": {"nodes_total": 3, "grounded": 0, "thin": 0, "unverified": 3,
                     "corpus_live": 0, "corpus_dead": 0, "corpus_needs_render": 0,
                     "corpus_total": 0, "structural_ok": True, "all_grounded": False},
    }
    monkeypatch.setattr(pipeline, "verify", lambda spec, out_dir: failed)
    result = pipeline.design_curriculum("calculus", out_root=str(tmp_path))
    assert result["decision"]["status"] == "blocked"
    assert active_path.read_text(encoding="utf-8") == generate_calculus().to_yaml()
    assert (tmp_path / "candidates" / "calculus.yaml").exists()


def test_blocked_candidate_extracted_cache_is_subject_and_fingerprint_isolated(tmp_path, monkeypatch):
    from open_tutor import pipeline
    from open_tutor.spec import save_yaml

    active = generate_calculus()
    active_path = tmp_path / "curriculum" / "calculus.yaml"
    active_path.parent.mkdir()
    save_yaml(active, str(active_path))
    active_cache = tmp_path / "cache" / "calculus" / "calculus-wiki-derivative.txt"
    active_cache.parent.mkdir(parents=True)
    active_cache.write_text("active cache", encoding="utf-8")
    report = {
        "subject": "calculus", "structural": {"ok": True, "errors": {}},
        "corpus": [{"id": source.id, "status": "live", "http_status": 200,
                    "method": "fixture", "needs_render": False,
                    "text_len": 14, "url": source.url, "text": "candidate text"}
                   for source in active.corpus],
        "nodes": [], "oracle_outputs": {},
        "summary": {"nodes_total": 3, "grounded": 0, "thin": 0, "unverified": 3,
                     "corpus_live": 4, "corpus_dead": 0, "corpus_needs_render": 0,
                     "corpus_total": 4, "structural_ok": True, "all_grounded": False},
    }
    monkeypatch.setattr(pipeline, "verify", lambda spec, out_dir: report)
    result = pipeline.design_curriculum("calculus", out_root=str(tmp_path))
    assert result["decision"]["status"] == "blocked"
    assert active_cache.read_text(encoding="utf-8") == "active cache"
    staged = list((tmp_path / "cache-candidates" / "calculus").glob("*/*.txt"))
    assert staged


def test_fake_local_completion_can_build_a_candidate_without_verdict_fields():
    from open_tutor.designer import generate_candidate

    source = {"id": "discovered-explicit-1", "name": "Public source",
              "url": "https://example.org/topic", "adapter": "explicit"}
    payload = {
        "subject": "linear-algebra", "title": "Linear Algebra",
        "scope": {"level": "beginner", "depth": "vectors", "assumed_prereqs": []},
        "corpus": [source["id"]],
        "nodes": [{"id": "vectors", "title": "Vectors",
                    "def": "A vector is an ordered mathematical object with components.",
                    "prereqs": [],
                    "misconceptions": [{"id": "vectors-m1", "text": "A vector is only a geometric arrow."}],
                    "grounding_corpus": [source["id"]], "oracle": None,
                    "covers_keywords": ["vector"]}],
    }

    class FakeClient:
        def complete(self, prompt):
            assert "vetted_sources" in prompt
            return json.dumps(payload)

    spec = generate_candidate("linear algebra", {source["id"]: source}, client=FakeClient())
    assert spec.subject == "linear-algebra"
    assert spec.nodes[0].status == "unknown"
    assert not spec.nodes[0].verification


def test_direct_local_client_rejects_public_endpoint_before_network():
    with pytest.raises(LocalModelError, match="private IP|local/private"):
        LocalCompletionClient("https://api.example.com/v1", "model")


def test_quantum_arxiv_stable_id_has_verified_metadata():
    from open_tutor.generator import generate_quantum_computing

    source = next(item for item in generate_quantum_computing().corpus
                  if item.id == "nielsen-chuang")
    assert "Universality in Quantum Computation" in source.name
    assert "Nielsen" not in source.name
    assert source.url == "https://arxiv.org/abs/quant-ph/9505018"


def test_arxiv_search_allows_slow_api_responses(monkeypatch):
    """arXiv's export API can take >20s per query (measured 21-27s on 2026-09-09).

    The search timeout must ride that out instead of failing every discovery.
    """
    atom = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<feed xmlns=\"http://www.w3.org/2005/Atom\">"
        "<entry><id>https://arxiv.org/abs/1706.03762v7</id>"
        "<title>Attention Is All You Need</title></entry></feed>"
    )
    captured: dict[str, Any] = {}

    def fake_fetch_text(url, *, timeout=15, max_bytes=1_000_000):
        captured["timeout"] = timeout
        result = network.FetchResult(url=url, status=200, headers={}, body=b"", final_url=url)
        return result, atom

    monkeypatch.setattr(source_adapters, "fetch_text", fake_fetch_text)
    rows = source_adapters.ArxivAdapter.search_preprints("attention is all you need")
    assert captured["timeout"] >= 45
    assert rows == [{"arxiv_id": "1706.03762v7",
                     "title": "Attention Is All You Need",
                     "url": "https://arxiv.org/pdf/1706.03762v7.pdf"}]


def test_empty_discovery_fails_closed_without_wasting_model_rounds(tmp_path, monkeypatch):
    """With zero vetted sources, candidate generation cannot succeed (every
    corpus entry must reference a vetted discovery ID) — fail fast instead of
    burning two model attempts on an impossible schema."""
    from open_tutor import pipeline

    monkeypatch.setattr(pipeline, "discover_sources", lambda topic: {
        "sources": [],
        "errors": ["arxiv search HTTP 0: TimeoutError: The read operation timed out"]})

    def forbidden_generate(*args, **kwargs):
        raise AssertionError("generate_candidate ran without vetted sources")

    monkeypatch.setattr(pipeline, "generate_candidate", forbidden_generate)
    result = pipeline.design_curriculum("thermodynamics", out_root=str(tmp_path))
    assert result["decision"]["status"] == "blocked"
    assert "source discovery produced no vetted public sources" in result["errors"]
    assert not any("candidate generation unavailable" in e for e in result["errors"])


def test_nonempty_discovery_still_reaches_candidate_generation(tmp_path, monkeypatch):
    """The fail-fast path must not swallow topics that DO have vetted sources."""
    from open_tutor import pipeline

    monkeypatch.setattr(pipeline, "discover_sources", lambda topic: {
        "sources": [{"id": "d1", "name": "Discovered", "url": "https://example.test/d",
                     "adapter": "test"}],
        "errors": []})
    seen: dict[str, Any] = {}

    def fake_generate(topic, records, **kwargs):
        seen["record_ids"] = sorted(records)
        raise pipeline.LocalModelError("model offline")

    monkeypatch.setattr(pipeline, "generate_candidate", fake_generate)
    result = pipeline.design_curriculum("thermodynamics", out_root=str(tmp_path))
    assert seen["record_ids"] == ["thermodynamics-d1"]
    assert result["decision"]["status"] == "blocked"
    assert any("candidate generation unavailable" in e for e in result["errors"])


def test_candidate_schema_retry_is_bounded_and_prompt_lists_trusted_registry():
    from open_tutor.designer import generate_candidate

    source = {"id": "discovered-explicit-2", "name": "Public source",
              "url": "https://example.org/topic", "adapter": "explicit"}
    payload = {
        "subject": "linear-algebra", "title": "Linear Algebra",
        "scope": {"level": "beginner", "depth": "vectors", "assumed_prereqs": []},
        "corpus": [source["id"]],
        "nodes": [{"id": "vectors", "title": "Vectors",
                    "def": "A vector is an ordered mathematical object with components.",
                    "prereqs": [], "misconceptions": [],
                    "grounding_corpus": [source["id"]], "oracle": None,
                    "covers_keywords": ["vector"]}],
    }

    class FakeClient:
        def __init__(self):
            self.calls = []

        def complete(self, prompt):
            self.calls.append(prompt)
            return "not-json" if len(self.calls) == 1 else json.dumps(payload)

    client = FakeClient()
    spec = generate_candidate("linear algebra", {source["id"]: source}, client=client)
    assert spec.subject == "linear-algebra"
    assert len(client.calls) == 2
    assert "trusted_oracles" in client.calls[0]
