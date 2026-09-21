"""Regression tests for public-source safety and typed source ingestion."""
from __future__ import annotations

import io

import pytest

from open_tutor import network, scraper
from open_tutor.network import FetchResult
from open_tutor.spec import CorpusSource, CurriculumSpec, Node, validate_identifier


def test_public_network_rejects_non_global_literal_targets():
    for url in (
        "http://100.64.0.1:9130/api/settings",  # CGNAT/Tailscale space
        "http://[::ffff:100.64.0.1]/",          # IPv4-mapped IPv6
        "http://224.0.0.1/",                        # multicast
        "http://192.0.2.1/",                        # documentation space
    ):
        result = network.validate_url(url)
        assert not result.ok, (url, result)
    assert network.validate_url("http://8.8.8.8/").ok


def test_safe_fetch_connects_to_the_address_it_validated(monkeypatch):
    calls: list[tuple[str, int]] = []

    def fake_getaddrinfo(host, port, **kwargs):
        calls.append((host, port))
        return [(network.socket.AF_INET, network.socket.SOCK_STREAM,
                 6, "", ("93.184.216.34", port))]

    def fake_request(url, address, *, timeout, max_bytes):
        assert address == "93.184.216.34"
        return FetchResult(url, 200, {"content-type": "text/plain"}, b"ok", url)

    monkeypatch.setattr(network.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(network, "_request_once", fake_request)
    result = network.safe_fetch("https://example.test/lesson", timeout=3, max_bytes=10)
    assert result.status == 200
    assert calls == [("example.test", 443)]


def test_mixed_dns_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (network.socket.AF_INET, network.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (network.socket.AF_INET, network.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 443)),
        ],
    )
    result = network.safe_fetch("https://example.test/lesson")
    assert result.status == 0
    assert "blocked" in (result.error or "")


@pytest.mark.parametrize("bad", [
    "../../escaped", "node/child", r"node\child", "/absolute", ".", "..",
    "line\nfeed", "C:\\escaped", "a:b",
])
def test_identifiers_are_safe_file_components(bad):
    with pytest.raises(ValueError):
        validate_identifier(bad)


def test_safe_stable_ids_and_direct_constructors_are_supported():
    source = CorpusSource("preskill-ph229", "Preskill", "https://example.test/preskill")
    node = Node("tensor_product", "Tensor product", "A tensor product combines spaces.",
                grounding_corpus=[source.id])
    spec = CurriculumSpec("quantum-computing", "Quantum Computing",
                          corpus=[source], nodes=[node])
    assert spec.subject == "quantum-computing"
    assert spec.nodes[0].id == "tensor_product"


def test_unsafe_ids_fail_before_a_spec_can_reach_cache_code():
    with pytest.raises(ValueError, match="corpus id"):
        CorpusSource("../../escaped", "Escaped", "https://example.test")
    with pytest.raises(ValueError, match="node id"):
        Node("../../escaped", "Escaped", "definition")
    with pytest.raises(ValueError, match="subject"):
        CurriculumSpec("../../escaped", "Escaped")
    with pytest.raises(ValueError, match="duplicate corpus"):
        CurriculumSpec(
            "safe-subject",
            "Safe",
            corpus=[
                CorpusSource("same", "One", "https://example.test/one"),
                CorpusSource("same", "Two", "https://example.test/two"),
            ],
        )


def test_from_dict_accepts_legacy_def_spelling_but_validates_ids():
    spec = CurriculumSpec.from_dict({
        "subject": "safe-subject",
        "title": "Safe",
        "corpus": [{"id": "source_id", "name": "Source", "url": "https://example.test"}],
        "nodes": [{"id": "node-id", "title": "Node", "def": "A definition."}],
    })
    assert spec.nodes[0].defn == "A definition."
    with pytest.raises(ValueError, match="corpus id"):
        CurriculumSpec.from_dict({
            "subject": "safe-subject",
            "title": "Unsafe",
            "corpus": [{"id": "../../escaped", "name": "x", "url": "https://example.test"}],
        })


def test_plaintext_is_not_parsed_as_html(monkeypatch):
    body = b"*** START OF THE PROJECT GUTENBERG EBOOK ***\n" + b"quantum text " * 40
    monkeypatch.setattr(
        scraper,
        "safe_fetch",
        lambda *args, **kwargs: FetchResult(
            "https://example.test/book.txt", 200, {"content-type": "text/plain; charset=utf-8"},
            body, "https://example.test/book.txt"),
    )
    extracted = scraper.extract("https://example.test/book.txt")
    assert extracted.method == "plaintext"
    assert extracted.ok
    assert "&lt;" not in extracted.body
    assert "quantum text" in extracted.body


def _real_pdf_bytes() -> bytes:
    """Create a tiny real PDF fixture using pypdf's writer primitives."""
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font),
    })})
    page[NameObject("/Resources")] = writer._add_object(resources)
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 280 Td (A qubit is a two-level quantum system.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_generated_pdf_is_extracted_or_reports_optional_dependency(monkeypatch):
    pdf = _real_pdf_bytes()
    monkeypatch.setattr(
        scraper,
        "safe_fetch",
        lambda *args, **kwargs: FetchResult(
            "https://example.test/lesson.pdf", 200, {"content-type": "application/pdf"},
            pdf, "https://example.test/lesson.pdf"),
    )
    extracted = scraper.extract("https://example.test/lesson.pdf", min_body=20)
    assert extracted.http_status == 200
    assert extracted.method == "pdf"
    if extracted.error and "optional dependency" in extracted.error:
        assert not extracted.ok
    else:
        assert extracted.ok
        assert extracted.body_len == len(extracted.body)
        assert "qubit" in extracted.body.lower()


def test_pdf_claim_without_signature_is_visible_failure(monkeypatch):
    monkeypatch.setattr(
        scraper,
        "safe_fetch",
        lambda *args, **kwargs: FetchResult(
            "https://example.test/bad.pdf", 200, {"content-type": "application/pdf"},
            b"not a pdf", "https://example.test/bad.pdf"),
    )
    extracted = scraper.extract("https://example.test/bad.pdf")
    assert extracted.ok is False
    assert extracted.method == "pdf"
    assert "signature" in (extracted.error or "")


def test_image_only_and_encrypted_pdf_fail_visibly(monkeypatch):
    pypdf = pytest.importorskip("pypdf")

    blank_writer = pypdf.PdfWriter()
    blank_writer.add_blank_page(width=300, height=300)
    blank_output = io.BytesIO()
    blank_writer.write(blank_output)
    monkeypatch.setattr(
        scraper,
        "safe_fetch",
        lambda *args, **kwargs: FetchResult(
            "https://example.test/image-only.pdf", 200, {"content-type": "application/pdf"},
            blank_output.getvalue(), "https://example.test/image-only.pdf"),
    )
    image_only = scraper.extract("https://example.test/image-only.pdf")
    assert image_only.ok is False
    assert "no extractable text" in (image_only.error or "")

    encrypted_writer = pypdf.PdfWriter()
    encrypted_writer.add_blank_page(width=300, height=300)
    encrypted_writer.encrypt("secret")
    encrypted_output = io.BytesIO()
    encrypted_writer.write(encrypted_output)
    monkeypatch.setattr(
        scraper,
        "safe_fetch",
        lambda *args, **kwargs: FetchResult(
            "https://example.test/encrypted.pdf", 200, {"content-type": "application/pdf"},
            encrypted_output.getvalue(), "https://example.test/encrypted.pdf"),
    )
    encrypted = scraper.extract("https://example.test/encrypted.pdf")
    assert encrypted.ok is False
    assert "encrypted" in (encrypted.error or "")


def test_render_channel_is_configurable_and_restricted(monkeypatch):
    monkeypatch.setenv("OPEN_TUTOR_RENDER_CHANNEL", "chrome")
    assert scraper._browser_channels() == ["chrome"]
    monkeypatch.setenv("OPEN_TUTOR_RENDER_CHANNEL", "not-a-browser")
    assert scraper._browser_channels() == []
    monkeypatch.delenv("OPEN_TUTOR_RENDER_CHANNEL")
    assert scraper._browser_channels() == [None, "chrome", "msedge"]


def test_render_rejects_unsafe_url_before_backend_call():
    called = False

    def backend(url):
        nonlocal called
        called = True
        return "<html><body>should not load</body></html>"

    result = scraper.browser_render("http://100.64.0.1:9130/api/settings", backend=backend)
    assert result.ok is False
    assert result.needs_render is True
    assert called is False
