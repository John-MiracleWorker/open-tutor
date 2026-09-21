"""The local FastAPI boundary for Open Tutor.

This module is intentionally an adapter, not a replacement for the verified
engine, assessment, state, compiler, or spec models.  Requests are bounded and
validated here; those existing modules own decisions about grounding, grading,
and mastery.  SQLite is the durable source for conversations and jobs, while
verified curriculum artifacts are imported into the same local database.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import ipaddress
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import assessment, learner_events, learner_state
from .api_helpers import bounded_text, local_endpoint, valid_identifier, valid_uuid_hex
from .compiler import MIN_GROUNDING_CHARS, spec_content_hash
from .designer import LocalCompletionClient
from .engine import tutor
from .llm import validate_local_endpoint
from .pipeline import _write_corpus_cache, design_curriculum
from .spec import CurriculumSpec, load_yaml
from .storage import AskAdmissionConflict, CorruptAdaptiveState, Database, utc_now
from .teaching import (
    TeachingState,
    compose_hint,
    compose_transfer,
    generate_teaching,
    readable_teaching_draft,
    select_plan,
    validate_teaching_output,
)
from .verifier import verify

MAX_BODY_BYTES = 1_000_000
MAX_QUESTION_CHARS = 12_000
MAX_RESPONSE_CHARS = 20_000
MAX_TOPIC_CHARS = 300
VERSION = "0.1.0"


class ThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=200)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    node_id: str | None = Field(default=None, max_length=64)
    followup_context: dict[str, Any] | None = None
    teaching: bool = False
    action: Literal[
        "respond", "simpler", "another-way", "hint", "example", "visual", "challenge",
        "got-it", "confused"
    ] = "respond"
    approach: Literal[
        "auto", "plain", "socratic", "worked-example", "analogy", "visual", "challenge"
    ] | None = None


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"]
    approach: Literal[
        "auto", "plain", "socratic", "worked-example", "analogy", "visual", "challenge"
    ]
    pace: Literal["gentle", "balanced", "brisk"]
    goal: str = Field(max_length=500)
    experience: str = Field(max_length=500)
    interests: str = Field(max_length=500)


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS)
    level: str | None = Field(default=None, max_length=100)
    depth: str | None = Field(default=None, max_length=100)
    review: bool = False
    sources: list[str] | None = Field(default=None, max_length=20)


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    fingerprint: str | None = None


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=128)
    response: str | dict[str, Any] = Field(max_length=MAX_RESPONSE_CHARS)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=200)
    mode: str | None = None
    # `configured` is a derived GET-only flag that settings clients echo back;
    # accepting and ignoring it keeps the full GET response PUT-able.
    configured: bool | None = None


def _request_text(response: str | dict[str, Any]) -> str:
    if isinstance(response, str):
        return bounded_text(response, MAX_RESPONSE_CHARS, "response")
    text = response.get("text")
    return bounded_text(text, MAX_RESPONSE_CHARS, "response.text") if isinstance(text, str) else ""


def _request_citations(response: str | dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(response, dict) or not isinstance(response.get("citations"), list):
        return []
    return [value for value in response["citations"] if isinstance(value, dict)]


def _citation_text(spec: CurriculumSpec, report: dict[str, Any], root: Path,
                   source_id: str) -> str:
    for row in report.get("corpus", []):
        if row.get("id") == source_id and isinstance(row.get("text"), str):
            return row["text"]
    path = root / "cache" / spec.subject / f"{source_id}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _validated_assessment_citations(
    item: dict[str, Any], spec: CurriculumSpec, report: dict[str, Any],
    root: Path, raw_citations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate source ids and exact corpus offsets for assessment evidence.

    A citation marker is not evidence by itself.  The submitted excerpt must
    be an exact slice of the verifier-measured corpus text; otherwise the
    attempt is unsupported and cannot certify a positive assessment result.
    """
    allowed = set(item.get("required_citations") or [])
    valid: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, citation in enumerate(raw_citations):
        source_id = citation.get("source_id")
        start, end, excerpt = citation.get("char_start"), citation.get("char_end"), citation.get("text")
        if source_id not in allowed:
            issues.append(f"citation-{index}: source is not required by this item")
            continue
        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(excerpt, str):
            issues.append(f"citation-{index}: exact text and integer offsets are required")
            continue
        corpus = _citation_text(spec, report, root, source_id)
        if start < 0 or end <= start or end > len(corpus) or corpus[start:end] != excerpt:
            issues.append(f"citation-{index}: excerpt/offset does not match measured corpus text")
            continue
        valid.append({"source_id": source_id, "char_start": start, "char_end": end,
                      "text": excerpt})
    if allowed and not valid:
        issues.append("citation-coverage: no exact required corpus span was supplied")
    return valid, issues


def _assessment_supported(item: dict[str, Any], text: str,
                          citations: list[dict[str, Any]], issues: list[str]) -> bool:
    """Certify the assessment envelope, never the learner's semantic answer.

    Semantic correctness is decided by ``assessment.grade`` against its
    server-owned key.  Numeric attempts are accepted even when wrong so they
    can become deterministic ``incorrect`` reviews instead of being mislabeled
    as unsupported; malformed/ambiguous attempts remain uncertified.
    """
    if not text.strip():
        issues.append("empty-response: no learner attempt")
        return False
    if item.get("quantitative"):
        import re as _re

        if len(_re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)) != 1:
            issues.append("ambiguous-response: quantitative answer must contain exactly one number")
            return False
        return True
    if item.get("kind") == assessment.KIND_TEACHBACK:
        # A non-empty explanation is a scorable attempt. Exact source spans
        # remain required for a fully supported result; assessment.grade will
        # apply the citation penalty. This lets a clearly wrong explanation be
        # recorded as incorrect instead of hiding it as an unsupported blank.
        return bool(text.strip())
    return bool(citations) or not item.get("required_citations")


def _assessment_source_id(spec: CurriculumSpec, report: dict[str, Any],
                          node) -> str | None:
    """Choose a substantive, live node source for an assessment item.

    Source order in a curriculum is not a relevance ranking: in the reference
    curriculum an abstract/TOC source precedes the actual concept article.
    Prefer measured text at the grounding floor and fall back only so synthetic
    fixtures can still issue an honest, later-flagged item.
    """
    corpus = {row.get("id"): row for row in report.get("corpus", [])}
    fallback = None
    for source_id in node.grounding_corpus:
        row = corpus.get(source_id, {})
        if fallback is None:
            fallback = source_id
        text = row.get("text")
        if (row.get("status") == "live" and isinstance(text, str)
                and len(text) >= MIN_GROUNDING_CHARS):
            return source_id
    return fallback


def _assessment_evidence(spec: CurriculumSpec, report: dict[str, Any], root: Path,
                         node, source_id: str | None) -> list[dict[str, Any]]:
    """Return one exact measured source span the browser can cite.

    This is deliberately an excerpt with offsets, not a citation flag.  The
    POST path rechecks the slice against the same measured corpus text.
    """
    if not source_id:
        return []
    text = _citation_text(spec, report, root, source_id)
    if len(text) < MIN_GROUNDING_CHARS:
        return []
    keywords = [word.casefold() for word in node.covers_keywords if word]
    sentences = list(re.finditer(r"[^.!?\n]{25,}[.!?]", text))
    chosen = None
    for match in sentences:
        sentence = match.group(0).strip()
        lower = sentence.casefold()
        if any(keyword in lower for keyword in keywords):
            chosen = (match.start(), match.end(), sentence)
            if re.search(r"\b(?:is|are|can be|defined)\b", lower):
                break
    if chosen is None:
        start = text.casefold().find(keywords[0]) if keywords else 0
        start = max(0, start)
        end = min(len(text), start + 600)
        while end < len(text) and text[end] not in ".!?":
            end += 1
        chosen = (start, end, text[start:end].strip())
    start, end, excerpt = chosen
    start = text.find(excerpt, max(0, start - 1))
    end = start + len(excerpt)
    return [{"source_id": source_id, "char_start": start, "char_end": end,
             "text": excerpt}]


def _as_spec(raw: dict[str, Any]) -> CurriculumSpec:
    return CurriculumSpec.from_dict(raw)


def _prior_ready_context(messages: list[dict[str, Any]], pending_question: str) -> tuple[list[int], str | None]:
    """Find the ready turn that delivered the pending task.

    A composed hint quotes only the passages that actually set the current
    task, so the learner is pointed at the checked sources behind their own
    question instead of model prose.
    """
    for message in reversed(messages):
        answer = message.get("answer")
        teaching = (answer or {}).get("teaching") or {}
        activity = teaching.get("activity") or {}
        if teaching.get("status") == "ready" and activity.get("prompt") == pending_question:
            refs = teaching.get("evidence_refs") or []
            if all(type(ref) is int for ref in refs):
                return refs, activity.get("kind")
    return [], None


def _teaching_evidence(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose numbered source data without turning it into model authority."""
    citations = answer.get("citations") or []
    return [
        {**citation, "ref": index}
        for index, citation in enumerate(citations, 1)
        if isinstance(citation, dict)
    ]


def _fallback_teaching(reason: str) -> dict[str, Any]:
    return {
        "title": "Let’s keep working from the evidence",
        "explanation": "The local teaching model was not available, so no unverified explanation was invented.",
        "steps": [], "diagram": None, "activity": None, "evidence_refs": [],
        "_reason": reason,
    }


def retained_teaching_state(
    old_state: TeachingState, node_from_evidence: str | None, last_action: str,
) -> dict[str, Any]:
    """Durable state after a failed (blocked or fallback) teaching turn.

    A failure is not a new lesson.  The learner's pending question and
    counters survive whenever the failure cannot prove a concept change:
    keep the prior state on the same resolved concept, and also when the
    evidence resolved no concept at all.  Only a genuinely different resolved
    concept may reset the thread's teaching context.
    """
    same_concept = old_state.node_id is not None and old_state.node_id == node_from_evidence
    unresolved = node_from_evidence is None and old_state.node_id is not None
    state = (
        old_state if same_concept or unresolved
        else TeachingState(node_id=node_from_evidence)
    ).to_dict()
    state["last_action"] = last_action
    return state


def _teaching_envelope(
    evidence: dict[str, Any], plan, teaching: dict[str, Any], *, status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the additive adaptive envelope; its top-level verdict is always false."""
    teaching = dict(teaching)
    latency = teaching.pop("_latency_ms", 0)
    server_status = "ready" if status == "coaching" else status.removeprefix("coaching-")
    teaching_payload = {
        "schema_version": "1", "status": server_status, "verified": False,
        "approach": plan.approach, "pace": plan.pace, "intent": plan.intent,
        "reason": reason or plan.reason, "title": teaching.get("title", "Teaching"),
        "explanation": teaching.get("explanation", ""), "steps": teaching.get("steps", []),
        "diagram": teaching.get("diagram"), "activity": teaching.get("activity"),
        "evidence_refs": teaching.get("evidence_refs", []),
        "warnings": ([] if status == "coaching" else [reason or "teaching degraded"]),
        "model": None, "latency_ms": latency,
    }
    envelope = {key: value for key, value in evidence.items() if key != "verification"}
    envelope.update({
        "grounded": False, "status": status,
        "verification_scope": "evidence-only",
        "evidence_grounded": bool(evidence.get("grounded")) and (
            not evidence.get("status") or evidence.get("status") == "grounded"
        ), "evidence": evidence,
        "draft": readable_teaching_draft(teaching), "teaching": teaching_payload,
    })
    return envelope


def _verified_artifacts(
    spec: CurriculumSpec, report: dict[str, Any], decision: dict[str, Any] | None = None
) -> tuple[bool, str]:
    """Validate an imported receipt/report without trusting generated flags.

    This is deliberately strict about the evidence already measured by the
    verifier.  A missing receipt, moved grounding bar, dead source, or failed
    node cannot become an active subject merely because a spec parses.
    """
    if not isinstance(report, dict) or report.get("subject") != spec.subject:
        return False, "report-subject-mismatch"
    summary = report.get("summary") or {}
    if summary.get("all_grounded") is not True:
        return False, "report-not-all-grounded"
    if summary.get("structural_ok") is False:
        return False, "report-structural-failure"
    corpus = report.get("corpus") or []
    nodes = report.get("nodes") or []
    if {c.get("id") for c in corpus} != {c.id for c in spec.corpus}:
        return False, "report-corpus-set-mismatch"
    if {n.get("id") for n in nodes} != {n.id for n in spec.nodes}:
        return False, "report-node-set-mismatch"
    if summary.get("nodes_total") != len(spec.nodes) or summary.get("grounded") != len(spec.nodes):
        return False, "report-node-count-mismatch"
    if summary.get("corpus_total") not in (None, len(spec.corpus)):
        return False, "report-corpus-count-mismatch"
    if any(n.get("status") != "grounded" for n in nodes):
        return False, "report-node-not-grounded"
    if decision is not None and decision.get("status") not in {"compiled", "paused"}:
        return False, "decision-not-usable"
    return True, "ok"


def _state_view(spec: CurriculumSpec, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the existing LearnerState projection to keyed nodes for API use."""
    if raw is None:
        raw = {
            "subject": spec.subject,
            "events": [],
            "attempts": {},
            "misconceptions_triggered": {},
            "mastery": {},
            "schedules": {},
        }
    raw = dict(raw)
    mastery = raw.get("mastery") or {}
    attempts = raw.get("attempts") or {}
    misconceptions = raw.get("misconceptions_triggered") or {}
    schedules = raw.get("schedules") or {}
    practices: dict[str, set[str]] = {}
    for event in raw.get("events", []):
        if event.get("kind") == "assessment" and isinstance(event.get("item_id"), str):
            practices.setdefault(event.get("node_id"), set()).add(event["item_id"])
    nodes: dict[str, Any] = {}
    for node in spec.nodes:
        schedule = schedules.get(node.id) or {}
        nodes[node.id] = {
            "id": node.id,
            "mastery": float(mastery.get(node.id, 0.0)),
            "attempts": int(attempts.get(node.id, 0)),
            "practice_attempts": len(practices.get(node.id, set())),
            "misconceptions_triggered": list(misconceptions.get(node.id, [])),
            "next_review": schedule.get("next_review"),
        }
    raw["subject"] = spec.subject
    raw["nodes"] = nodes
    return raw


def _gates_and_due(spec: CurriculumSpec, state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    # Keep the HTTP projection on the same deterministic gate/queue owners as
    # the durable learner state.  The API supplies the current instant only for
    # the time-filtered due queue; persisted event replay remains clock-free.
    gates = learner_state.gate_report(spec, state)
    due = learner_state.due_nodes(spec, state, now=utc_now())
    return gates, due


def _safe_candidate(
    spec: CurriculumSpec, report: dict[str, Any], decision: dict[str, Any], fingerprint: str
) -> dict[str, Any]:
    return {
        "subject": spec.subject,
        "decision": decision,
        "report": report,
        "fingerprint": fingerprint,
    }


def _discover_sources(query: str) -> dict[str, Any]:
    """Call an integrated source adapter when one exists; otherwise report it."""
    errors: list[str] = []
    fn = None
    for module_name in ("open_tutor.source_adapters", "open_tutor.designer"):
        try:
            module = __import__(module_name, fromlist=["discover_sources"])
            fn = getattr(module, "discover_sources", None)
            if callable(fn):
                break
        except ImportError:
            continue
    if fn is None:
        return {
            "sources": [],
            "errors": [
                "source-discovery-unavailable: no source_adapters.discover_sources integration"
            ],
        }
    try:
        found = fn(query)
    except Exception as exc:  # noqa: BLE001 - visible research failure
        return {"sources": [], "errors": [f"source-discovery-error: {type(exc).__name__}: {exc}"]}
    if isinstance(found, dict):
        errors.extend(str(error) for error in (found.get("errors") or []))
        found = found.get("sources") or []
    sources: list[dict[str, Any]] = []
    for index, value in enumerate(found or []):
        if isinstance(value, dict):
            url = value.get("url")
            if not isinstance(url, str):
                errors.append(f"source-{index}: missing url")
                continue
            sources.append(
                {
                    "id": value.get("id") or hashlib.sha256(url.encode()).hexdigest()[:12],
                    "name": value.get("name") or url,
                    "url": url,
                    "adapter": value.get("adapter") or "unknown",
                }
            )
        else:
            errors.append(f"source-{index}: adapter returned non-mapping")
    return {"sources": sources, "errors": errors}


def _load_filesystem_bundles(db: Database, root: Path) -> None:
    """Import only normal, compiled artifacts; candidates stay candidates."""
    cur = root / "curriculum"
    if not cur.is_dir():
        return
    for path in sorted(cur.glob("*.yaml")):
        subject = path.stem
        if not valid_identifier(subject):
            continue
        # SQLite owns accepted edits. A boot-time import must not roll an
        # approved candidate back to an older CLI-generated YAML artifact.
        if db.get_active_bundle(subject) is not None:
            continue
        try:
            spec = load_yaml(str(path))
            report_path = cur / f"{subject}.report.json"
            receipt_path = cur / f"{subject}.receipts.json"
            if not report_path.exists() or not receipt_path.exists():
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("invariant", {}).get("min_grounding_chars") != MIN_GROUNDING_CHARS:
                continue
            if receipt.get("spec_content_hash") != spec_content_hash(spec):
                continue
            if receipt.get("decision", {}).get("status") != "compiled":
                continue
            ok, _ = _verified_artifacts(spec, report, receipt.get("decision"))
            if ok:
                db.put_active_bundle(
                    spec, report, receipt.get("decision", {}), spec_content_hash(spec)
                )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            # A corrupt artifact is not a server crash and is not usable data.
            continue


def _call_design(fn, request: DesignRequest, out_root: str,
                 settings: dict[str, Any] | None = None):
    values = {
        "out_root": out_root,
        "review": request.review,
        "level": request.level,
        "depth": request.depth,
        "sources": request.sources,
    }
    params = inspect.signature(fn).parameters
    kwargs = {key: value for key, value in values.items() if key in params and value is not None}
    if "client" in params and settings:
        base = settings.get("base_url")
        model = settings.get("model")
        if base and model:
            kwargs["client"] = LocalCompletionClient(
                validate_local_endpoint(str(base)), str(model)
            )
    return fn(request.topic, **kwargs)


def create_app(
    data_dir: str | Path | None = None,
    *,
    path: str | Path | None = None,
    write_token: str | None = None,
    frontend_dir: str | Path | None = None,
) -> FastAPI:
    """Create an isolated application. No subject or learner data is seeded."""
    root = Path(data_dir or (Path(path).parent if path else "out"))
    db = Database(data_dir=root, path=path)
    _load_filesystem_bundles(db, root)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="open-tutor-job")
    configured_token = (
        write_token if write_token is not None else os.environ.get("OPEN_TUTOR_WRITE_TOKEN")
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        executor.shutdown(wait=True, cancel_futures=False)
        db.close()

    app = FastAPI(title="Open Tutor", version=VERSION, lifespan=lifespan)
    app.state.storage = db
    app.state.executor = executor
    app.state.data_dir = root
    app.state.write_token = configured_token
    assessment_lock = threading.RLock()
    review_lock = threading.RLock()

    @app.exception_handler(CorruptAdaptiveState)
    async def adaptive_state_error(request: Request, exc: CorruptAdaptiveState):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422, content={"detail": str(exc.errors()[0].get("msg", "invalid request"))}
        )

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        # Reject browser DNS-rebinding Host names before exposing even reads.
        # The deployed surface is numeric Tailscale; loopback is explicit test use.
        try:
            hostname = request.url.hostname or ""
            test_host = (hostname == "testserver" and request.client is not None
                         and request.client.host == "testclient")
            host_ok = hostname == "localhost" or test_host
            if not host_ok:
                address = ipaddress.ip_address(hostname)
                host_ok = address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            host_ok = False
        if not host_ok:
            return JSONResponse(status_code=400, content={"detail": "untrusted Host header"})
        # Bound memory while reading, not after an unbounded body() allocation.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "request body too large"})
            body.extend(chunk)
        request._body = bytes(body)  # Starlette's cached receive path for downstream parsing.
        return await call_next(request)

    def write_guard(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin.rstrip("/") != expected.rstrip("/"):
                raise HTTPException(status_code=403, detail="cross-origin write rejected")
        if request.headers.get("sec-fetch-site", "").lower() in {"cross-site", "外"}:
            raise HTTPException(status_code=403, detail="cross-origin write rejected")
        if configured_token:
            value = request.headers.get("authorization", "")
            supplied = value[7:] if value.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(supplied, configured_token):
                raise HTTPException(status_code=401, detail="write bearer token required")

    def bundle(subject: str) -> tuple[CurriculumSpec, dict[str, Any], dict[str, Any]]:
        if not valid_identifier(subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        raw = db.get_active_bundle(subject)
        if raw is None:
            raise HTTPException(status_code=404, detail="subject not found")
        try:
            return _as_spec(raw["spec"]), raw["report"], raw["decision"]
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"corrupt active curriculum: {exc}"
            ) from exc

    @app.get("/api/health")
    def health():
        settings = db.get_settings()
        return {
            "ok": True,
            "model": {
                "configured": bool(settings.get("base_url") and settings.get("model")),
                "base_url": settings.get("base_url"),
                "model": settings.get("model"),
            },
            "version": VERSION,
        }

    @app.get("/api/learner/preferences")
    def learner_preferences():
        return db.get_preferences()

    @app.put("/api/learner/preferences")
    def update_learner_preferences(body: PreferencesRequest, _: None = Depends(write_guard)):
        try:
            return db.put_preferences(body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/subjects")
    def subjects():
        active = {raw["spec"]["subject"]: raw for raw in db.list_active_bundles()}
        candidate_subjects = []
        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT subject FROM curricula WHERE candidate_spec IS NOT NULL"
            ).fetchall()
            candidate_subjects = [row["subject"] for row in rows]
        finally:
            conn.close()
        result = []
        for subject in sorted(set(active) | set(candidate_subjects)):
            raw = active.get(subject)
            candidate = db.get_candidate_bundle(subject)
            visible = raw or candidate
            assert visible is not None
            summary = visible["report"].get("summary", {})
            result.append(
                {
                    "subject": visible["spec"]["subject"],
                    "title": visible["spec"]["title"],
                    "node_count": len(visible["spec"].get("nodes", [])),
                    "grounded_count": summary.get("grounded", 0),
                    "source_count": len(visible["spec"].get("corpus", [])),
                    "status": raw["decision"].get("status", "compiled") if raw
                    else candidate["decision"].get("status", "paused"),
                    "candidate": (
                        {
                            "status": candidate["decision"].get("status"),
                            "fingerprint": candidate["fingerprint"],
                            "grounded_count": candidate["report"].get("summary", {}).get(
                                "grounded", 0
                            ),
                            "node_count": len(candidate["spec"].get("nodes", [])),
                            "reasons": candidate["decision"].get("reasons", []),
                        }
                        if candidate else None
                    ),
                }
            )
        return {"subjects": result}

    @app.get("/api/subjects/{subject}")
    def subject_detail(subject: str):
        if not valid_identifier(subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        active = db.get_active_bundle(subject)
        candidate = db.get_candidate_bundle(subject)
        if active is None and candidate is None:
            raise HTTPException(status_code=404, detail="subject not found")
        visible = active or candidate
        assert visible is not None
        spec, report, decision = (
            _as_spec(visible["spec"]), visible["report"], visible["decision"]
        )
        state = _state_view(spec, db.get_subject_state(subject))
        gates, due = _gates_and_due(spec, state)
        return {
            "spec": spec.to_dict(),
            "report": report,
            "decision": decision,
            "state": state,
            "gates": gates,
            "due": due,
            "candidate": (
                {
                    "spec": candidate["spec"], "report": candidate["report"],
                    "decision": candidate["decision"], "fingerprint": candidate["fingerprint"],
                }
                if candidate else None
            ),
        }

    @app.get("/api/threads")
    def threads(subject: str | None = Query(default=None, max_length=64)):
        if subject is not None and not valid_identifier(subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        return {"threads": db.list_threads(subject)}

    @app.post("/api/threads")
    def create_thread(body: ThreadRequest, _: None = Depends(write_guard)):
        if not valid_identifier(body.subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        bundle(body.subject)
        return db.create_thread(body.subject, body.title)

    @app.get("/api/threads/{thread_id}")
    def get_thread(thread_id: str):
        if not valid_uuid_hex(thread_id):
            raise HTTPException(status_code=400, detail="invalid thread identifier")
        result = db.get_thread(thread_id)
        if result is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return result

    def ask_worker(job_id: str, thread_id: str, question: str,
                   node_id: str | None, followup_context: dict[str, Any] | None):
        try:
            row = db.get_thread(thread_id)
            if row is None:
                raise RuntimeError("thread disappeared")
            spec, _, _ = bundle(row["thread"]["subject"])
            settings = db.get_settings()
            db.set_job_stage(job_id, "resolving")
            db.set_job_stage(job_id, "retrieving")
            db.set_job_stage(job_id, "preflight")
            # The engine owns the source-numbered, data-only model prompt and gate.
            db.set_job_stage(job_id, "drafting")
            answer = tutor(
                spec, question, cache_dir=str(root / "cache"), timeout=120.0,
                selected_node=node_id, followup_context=followup_context,
                mode=settings.get("mode", "extractive"),
                base_url=settings.get("base_url"), model=settings.get("model"),
            )
            db.set_job_stage(job_id, "verifying")
            answer_dict = answer.to_dict()
            events = learner_events.events_from_answer(answer, spec, learner_utterance=question)
            if events:
                # Share the assessment projection lock: chat may record a
                # validated misconception, but never a mastery/review signal.
                with assessment_lock:
                    state_path = root / "curriculum" / f"{spec.subject}.learnerstate.json"
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_store = learner_state.LearnerStateStore(str(state_path), spec)
                    recorded = state_store.record_events(events, now=utc_now())
                    if any(r.status == "rejected" for r in recorded):
                        raise RuntimeError("learner-state: tutor interaction event was rejected")
                    state = state_store.state()
                    if state is not None:
                        db.put_subject_state(spec.subject, state.to_dict())
            if not answer_dict.get("grounded", False):
                # The engine may return an honestly degraded answer; it is still
                # persisted, but the result never claims verification.
                pass
            db.complete_job(
                job_id, answer_dict, assistant=(answer_dict.get("draft", ""), answer_dict)
            )
        except Exception as exc:  # noqa: BLE001 - persisted first-class worker failure
            status = "model-error" if str(exc).startswith("model-error:") else "failed"
            db.fail_job(job_id, f"{type(exc).__name__}: {exc}", status=status)

    def teaching_worker(
        job_id: str, thread_id: str, question: str, node_id: str | None,
        action: str, approach: str | None,
    ):
        """Run evidence and coaching separately; only the former is verified."""
        try:
            row = db.get_thread(thread_id)
            if row is None:
                raise RuntimeError("thread disappeared")
            spec, _, _ = bundle(row["thread"]["subject"])
            settings = db.get_settings()
            preferences = db.get_preferences()
            old_state = TeachingState.from_dict(row.get("teaching_state"))
            # Short replies are pinned to the pending concept only when the
            # deterministic resolver cannot identify a changed concept. An
            # explicit node always wins after route validation.
            selected = node_id
            if selected is None and old_state.pending_question and len(question) <= 500:
                from .engine import resolve_node

                if not resolve_node(question, spec).resolved:
                    selected = old_state.node_id
            db.set_job_stage(job_id, "evidence")
            evidence_answer = tutor(
                spec, question, cache_dir=str(root / "cache"), timeout=120.0,
                selected_node=selected, followup_context=None, mode="extractive",
            )
            evidence = evidence_answer.to_dict()
            node_from_evidence = (evidence.get("resolution") or {}).get("node_id")
            plan = select_plan(
                question=question, action=action, approach=approach,
                preferences=preferences, state=old_state if node_from_evidence is not None else TeachingState(), node_id=node_from_evidence,
            )
            source_evidence = _teaching_evidence(evidence)
            evidence_status = evidence.get("status")
            evidence_ok = bool(evidence.get("grounded")) and (
                not evidence_status or evidence_status == "grounded"
            )
            if not evidence_ok:
                reason = "Teaching is blocked because the deterministic evidence result was not grounded."
                blocked = {
                    "title": "Teaching is blocked",
                    "explanation": "The source evidence or trusted oracle did not pass its independent checks.",
                    "steps": [], "diagram": None, "activity": None, "evidence_refs": [],
                }
                # A blocked turn is a first-class failure, not a new lesson:
                # keep the prior durable teaching state when it lives on the
                # same concept, or when the evidence resolved no concept at
                # all (an unresolved failure has no authority to reset the
                # learner's pending task).
                state = retained_teaching_state(old_state, node_from_evidence,
                                                plan.next_state.last_action)
                result = _teaching_envelope(evidence, plan, blocked,
                                            status="coaching-blocked", reason=reason)
                db.complete_job(job_id, result,
                                assistant=(result["draft"], result), teaching_state=state)
                return

            db.set_job_stage(job_id, "coaching")
            try:
                # A hint on a real pending task is host-composed: repeated
                # qualification showed model-authored hint prose naming the
                # answer.  Pointer/quote content comes only from checked
                # sources; no model inference runs for this turn.
                if plan.next_state.last_action == "hint" and plan.pending_question:
                    prior_refs, _ = _prior_ready_context(row.get("messages", []), plan.pending_question)
                    teaching = validate_teaching_output(
                        compose_hint(plan=plan, pending_question=plan.pending_question,
                                     evidence=source_evidence, prior_refs=prior_refs),
                        known_refs={item["ref"] for item in source_evidence if type(item.get("ref")) is int},
                    )
                    result = _teaching_envelope(evidence, plan, teaching, status="coaching")
                    next_state = plan.next_state.to_dict()
                    next_state["pending_question"] = teaching["activity"]["prompt"]
                    db.complete_job(job_id, result,
                                    assistant=(result["draft"], result), teaching_state=next_state)
                    return
                # A got-it transfer on a real pending task is host-composed:
                # live qualification showed the model near-repeating the prior
                # question as the "new situation".  The host frames a fresh
                # situation from the checked sources; no model inference runs.
                if plan.next_state.last_action == "got-it" and plan.pending_question:
                    prior_refs, _ = _prior_ready_context(row.get("messages", []), plan.pending_question)
                    teaching = validate_teaching_output(
                        compose_transfer(plan=plan, pending_question=plan.pending_question,
                                         evidence=source_evidence, prior_refs=prior_refs),
                        known_refs={item["ref"] for item in source_evidence if type(item.get("ref")) is int},
                    )
                    result = _teaching_envelope(evidence, plan, teaching, status="coaching")
                    next_state = plan.next_state.to_dict()
                    next_state["pending_question"] = teaching["activity"]["prompt"]
                    db.complete_job(job_id, result,
                                    assistant=(result["draft"], result), teaching_state=next_state)
                    return
                started = time.monotonic()
                teaching = generate_teaching(
                    plan=plan,
                    history=[{"role": message["role"], "content": message["content"][:1500]}
                             for message in row.get("messages", [])],
                    pending_question=plan.pending_question, request=question,
                    evidence=source_evidence, oracle=evidence.get("oracle"),
                    profile=preferences, base_url=settings.get("base_url"),
                    model=settings.get("model"),
                )
                # Keep the server's validator in the route even if an injected
                # provider seam returns a mapping instead of raw JSON.
                # Match the exact bounded source selection in the model prompt
                # and generate_teaching: only the first five evidence items are
                # exposed to the model, so only their refs are valid here.
                teaching = validate_teaching_output(
                    teaching,
                    known_refs={item["ref"] for item in source_evidence[:5]
                                if type(item.get("ref")) is int},
                ) | {"_latency_ms": max(0, round((time.monotonic() - started) * 1000))}
                result = _teaching_envelope(evidence, plan, teaching, status="coaching")
                result["teaching"]["model"] = settings.get("model")
                next_state = plan.next_state.to_dict()
                next_state["pending_question"] = (
                    teaching.get("activity", {}).get("prompt") if teaching.get("activity") else None
                )
                db.complete_job(job_id, result,
                                assistant=(result["draft"], result), teaching_state=next_state)
            except Exception as exc:  # noqa: BLE001 - visible local-model degradation
                reason = f"Local teaching model unavailable or invalid: {type(exc).__name__}."
                fallback = _fallback_teaching(reason)
                # Failed generation does not earn a completed teaching turn/hint.
                # Keep the last real activity on the same concept for retry, and
                # also when the evidence resolved no concept at all: an
                # unresolved failure has no authority to reset the learner's
                # pending task.
                state = retained_teaching_state(old_state, node_from_evidence,
                                                plan.next_state.last_action)
                result = _teaching_envelope(evidence, plan, fallback,
                                            status="coaching-fallback", reason=reason)
                db.complete_job(job_id, result,
                                assistant=(result["draft"], result), teaching_state=state)
        except Exception as exc:  # noqa: BLE001 - durable first-class worker failure
            status = "model-error" if str(exc).startswith("model-error:") else "failed"
            db.fail_job(job_id, f"{type(exc).__name__}: {exc}", status=status)

    @app.post("/api/threads/{thread_id}/ask")
    def ask(thread_id: str, body: AskRequest, _: None = Depends(write_guard)):
        if not valid_uuid_hex(thread_id):
            raise HTTPException(status_code=400, detail="invalid thread identifier")
        row = db.get_thread(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="thread not found")
        if body.node_id is not None and row["thread"]["subject"]:
            thread_spec, _, _ = bundle(row["thread"]["subject"])
            if thread_spec.node(body.node_id) is None:
                raise HTTPException(status_code=400, detail="node_id not found in subject")
        question = bounded_text(body.question, MAX_QUESTION_CHARS, "question")
        if body.teaching and body.followup_context is not None:
            raise HTTPException(
                status_code=400,
                detail="adaptive teaching uses canonical persisted history; followup_context is not accepted",
            )
        context = body.followup_context
        # Adaptive requests use canonical history in teaching_worker, not the
        # legacy context. JSON escaping can enlarge even truncated messages.
        if context is None and not body.teaching:
            context = {"messages": [
                {"role": m["role"], "content": m["content"][:1500]}
                for m in row.get("messages", [])[-4:]
            ]}
        if context is not None and len(json.dumps(context, ensure_ascii=False)) > 8_000:
            raise HTTPException(status_code=413, detail="followup_context exceeds the 8000-character limit")
        try:
            admission = db.admit_ask(
                thread_id, row["thread"]["subject"], question,
                {"question": question, "node_id": body.node_id, "teaching": body.teaching,
                 "action": body.action, "approach": body.approach},
            )
        except AskAdmissionConflict as exc:
            raise HTTPException(status_code=409, detail="thread already has an in-flight ask") from exc
        job = admission["job"]
        if body.teaching:
            executor.submit(teaching_worker, job["id"], thread_id, question,
                            body.node_id, body.action, body.approach)
        else:
            executor.submit(ask_worker, job["id"], thread_id, question, body.node_id, context)
        return {"job_id": job["id"]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if not valid_uuid_hex(job_id):
            raise HTTPException(status_code=400, detail="invalid job identifier")
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {key: job[key] for key in ("id", "status", "stage", "error", "result")}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str):
        if not valid_uuid_hex(job_id):
            raise HTTPException(status_code=400, detail="invalid job identifier")
        if db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        try:
            initial_seq = int(request.headers.get("last-event-id", "0"))
        except ValueError:
            initial_seq = 0

        async def stream():
            seq = max(initial_seq, 0)
            while True:
                events = db.get_job_events(job_id, seq)
                for event in events:
                    seq = event["seq"]
                    yield f"id: {seq}\nevent: {event['event']}\ndata: {json.dumps(event['payload'])}\n\n"
                job = db.get_job(job_id)
                if job is None or job["status"] not in {"queued", "running"}:
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def design_worker(job_id: str, body: DesignRequest):
        backup_report: bytes | None = None
        report_path = (
            root / "curriculum" / f"{body.topic.strip().lower().replace(' ', '-')}.report.json"
        )
        try:
            db.set_job_stage(job_id, "scoping")
            db.set_job_stage(job_id, "researching")
            report_path.parent.mkdir(parents=True, exist_ok=True)
            if report_path.exists():
                backup_report = report_path.read_bytes()
            db.set_job_stage(job_id, "verifying")
            raw = _call_design(design_curriculum, body, str(root), db.get_settings())
            if not isinstance(raw, dict):
                raise TypeError("designer returned no result mapping")
            spec_path = raw.get("spec_path") or raw.get("candidate_spec_path")
            if spec_path and Path(spec_path).exists():
                generated_spec = load_yaml(spec_path)
            elif isinstance(raw.get("spec"), dict):
                generated_spec = _as_spec(raw["spec"])
            else:
                errors = raw.get("errors") or [
                    "designer result omitted spec_path, candidate_spec_path, and spec"]
                raise RuntimeError("designer-error: " + "; ".join(map(str, errors)))
            report = raw.get("report")
            if report is None and raw.get("report_path") and Path(raw["report_path"]).exists():
                report = json.loads(Path(raw["report_path"]).read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise TypeError("designer result omitted verifier report")
            decision = raw.get("decision") or {}
            fingerprint = spec_content_hash(generated_spec)
            ok, _reason = _verified_artifacts(generated_spec, report, decision)
            if decision.get("status") == "compiled" and ok:
                db.put_active_bundle(generated_spec, report, decision, fingerprint)
            else:
                db.put_candidate_bundle(generated_spec, report, decision, fingerprint)
            result = {
                "subject": generated_spec.subject,
                "decision": decision,
                "report": report,
                "fingerprint": fingerprint,
            }
            db.complete_job(job_id, result)
        except SystemExit as exc:
            db.fail_job(job_id, str(exc), status="failed")
        except Exception as exc:  # noqa: BLE001 - visible design failure
            db.fail_job(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            if backup_report is not None and report_path.exists():
                report_path.write_bytes(backup_report)

    @app.post("/api/design")
    def design(body: DesignRequest, _: None = Depends(write_guard)):
        topic = bounded_text(body.topic, MAX_TOPIC_CHARS, "topic")
        job = db.create_job("design", topic, input_data=body.model_dump())
        executor.submit(design_worker, job["id"], body)
        return {"job_id": job["id"]}

    @app.get("/api/research")
    def research(query: str = Query(min_length=1, max_length=300)):
        return _discover_sources(bounded_text(query, 300, "query"))

    @app.post("/api/subjects/{subject}/review")
    def review(subject: str, body: ReviewRequest, _: None = Depends(write_guard)):
        if not valid_identifier(subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        if body.action not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="action must be approve or reject")
        candidate = db.get_candidate_bundle(subject)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        fingerprint = candidate["fingerprint"]
        if body.fingerprint and not hmac.compare_digest(body.fingerprint, fingerprint):
            raise HTTPException(status_code=409, detail="candidate fingerprint changed")
        if body.action == "reject":
            db.reject_candidate(subject, fingerprint)
            return {"decision": {"status": "rejected", "fingerprint": fingerprint}}
        try:
            candidate_spec = _as_spec(candidate["spec"])
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"candidate invalid: {exc}") from exc
        if spec_content_hash(candidate_spec) != fingerprint:
            raise HTTPException(status_code=409, detail="candidate fingerprint invalid")
        ok, reason = _verified_artifacts(candidate_spec, candidate["report"], candidate["decision"])
        if not ok:
            raise HTTPException(status_code=409, detail=f"approval blocked: {reason}")
        with review_lock:
            # Reports produced by the verifier carry measured text. Require
            # the staged cache for those candidates before changing the active
            # database bundle; a blocked candidate therefore cannot poison the
            # active retrieval path.
            measured = any(isinstance(row.get("text"), str) and row.get("text", "").strip()
                           for row in (candidate["report"].get("corpus") or []))
            staged = root / "cache-candidates" / subject / fingerprint
            if measured and not staged.is_dir():
                raise HTTPException(status_code=409,
                                    detail="approval blocked: candidate extracted cache is missing")
            promoted = db.promote_candidate(subject, fingerprint)
            if promoted is None:
                raise HTTPException(status_code=409, detail="candidate changed before approval")
            if staged.is_dir():
                active_cache = root / "cache" / subject
                active_cache.mkdir(parents=True, exist_ok=True)
                for source_file in staged.glob("*.txt"):
                    shutil.copy2(source_file, active_cache / source_file.name)
            decision = dict(promoted["decision"])
            decision.update({"status": "compiled", "approved": True, "fingerprint": fingerprint})
            db.put_active_bundle(_as_spec(promoted["spec"]), promoted["report"], decision, fingerprint)
            return {"decision": decision}

    def candidate_worker(job_id: str, subject: str, candidate_spec: CurriculumSpec):
        try:
            db.set_job_stage(job_id, "verifying")
            report = verify(candidate_spec, out_dir=str(root / "oracle_outputs"))
            active = db.get_active_bundle(subject)
            old = _as_spec(active["spec"]) if active else None
            from .compiler import decide

            decision = decide(report, old, candidate_spec, review=True).to_dict()
            fingerprint = spec_content_hash(candidate_spec)
            _write_corpus_cache(
                candidate_spec, report, str(root / "cache"),
                subject_dir=str(root / "cache-candidates" / subject / fingerprint),
            )
            db.put_candidate_bundle(candidate_spec, report, decision, fingerprint)
            db.complete_job(
                job_id,
                {
                    "subject": subject,
                    "decision": decision,
                    "report": report,
                    "fingerprint": fingerprint,
                },
            )
        except Exception as exc:  # noqa: BLE001
            db.fail_job(job_id, f"{type(exc).__name__}: {exc}")

    @app.put("/api/subjects/{subject}/candidate")
    def put_candidate(subject: str, body: CandidateRequest, _: None = Depends(write_guard)):
        if not valid_identifier(subject):
            raise HTTPException(status_code=400, detail="invalid subject identifier")
        try:
            candidate_spec = _as_spec(body.spec)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid curriculum spec: {exc}") from exc
        if candidate_spec.subject != subject:
            raise HTTPException(status_code=400, detail="candidate subject does not match route")
        job = db.create_job(
            "candidate-edit", subject, input_data={"fingerprint": spec_content_hash(candidate_spec)}
        )
        executor.submit(candidate_worker, job["id"], subject, candidate_spec)
        return {"job_id": job["id"]}

    @app.get("/api/subjects/{subject}/assessment")
    def issue_assessment(
        subject: str, node_id: str = Query(..., max_length=64), kind: str = Query("quiz")
    ):
        spec, report, _ = bundle(subject)
        kind = {
            "teach-back": assessment.KIND_TEACHBACK,
            "teachback": assessment.KIND_TEACHBACK,
            "quiz": assessment.KIND_QUIZ,
        }.get(kind, kind)
        if kind not in {assessment.KIND_QUIZ, assessment.KIND_TEACHBACK}:
            raise HTTPException(status_code=400, detail="kind must be quiz or teach-back")
        node = spec.node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        gate = learner_state.gate_report(spec, db.get_subject_state(subject)).get(node.id, {})
        if not gate.get("unlocked", False):
            raise HTTPException(
                status_code=409,
                detail="assessment locked by prerequisites: "
                + ", ".join(gate.get("blocking_prereqs", [])),
            )
        source_id = _assessment_source_id(spec, report, node)
        source_ids = [source_id] if source_id else []
        if kind == assessment.KIND_TEACHBACK:
            item = assessment.make_item(
                kind,
                node.id,
                f"Explain {node.title} in your own words.",
                required_citations=source_ids,
                rubric_points=(
                    ["two-level", "basis", "superposition"]
                    if node.id == "qubit" else list(node.covers_keywords[:3])
                ),
                item_id=f"teachback-{node.id}-{uuid.uuid4().hex}",
            )
        else:
            if node.id == "qubit":
                item = assessment.make_item(
                    kind, node.id,
                    "Which statement best describes a qubit? Reply with A, B, C, or D.",
                    quantitative=False, required_citations=source_ids,
                    answer_options=[
                        "A. A classical bit whose value is hidden from us.",
                        "B. A two-level quantum system that can be in a coherent superposition of basis states.",
                        "C. A particle that must spin physically clockwise or counterclockwise.",
                        "D. A register that always stores two classical bits.",
                    ],
                    trusted_answer=["b"],
                    item_id=f"quiz-{node.id}-{uuid.uuid4().hex}",
                )
            else:
                # Registry output is not automatically a comprehensible scalar
                # answer.  Do not grade an arbitrary first field; use the
                # citation-backed teach-back contract for those nodes.
                item = assessment.make_item(
                    assessment.KIND_TEACHBACK, node.id,
                    f"Explain {node.title} in your own words.",
                    required_citations=source_ids,
                    rubric_points=list(node.covers_keywords[:3]),
                    item_id=f"teachback-{node.id}-{uuid.uuid4().hex}",
                )
        valid, issues = assessment.validate_item(item, spec)
        if not valid:
            raise HTTPException(
                status_code=500, detail="assessment issuance failed: " + "; ".join(issues)
            )
        db.put_assessment_item(subject, item)
        public = dict(item)
        public.pop("trusted_answer", None)
        return {
            "item": public,
            "evidence": _assessment_evidence(spec, report, root, node, source_id),
        }

    @app.post("/api/subjects/{subject}/assessment")
    def submit_assessment(subject: str, body: AssessmentRequest, _: None = Depends(write_guard)):
        spec, report, _ = bundle(subject)
        item = db.get_assessment_item(subject, body.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="assessment item not found or expired")
        with assessment_lock:
            cached = db.get_assessment_result(subject, body.item_id)
            if cached is not None:
                return cached
            valid, issues = assessment.validate_item(item, spec)
            if not valid:
                raise HTTPException(
                    status_code=409, detail="stored assessment invalid: " + "; ".join(issues)
                )
            text = _request_text(body.response)
            citations, citation_issues = _validated_assessment_citations(
                item, spec, report, root, _request_citations(body.response)
            )
            support_issues = list(citation_issues)
            supported = _assessment_supported(item, text, citations, support_issues)
            verification = {
                "grounded": supported,
                "issues": support_issues,
                "citations_used": list(range(1, len(citations) + 1)),
            }
            response_obj = assessment.LearnerResponse(
                text=text, grounded=supported, citations=citations,
                verification=verification,
            )
            grade = assessment.grade(item, response_obj, spec)
            # Only the validated T4 event factory may mutate learner state.  A
            # wrong but well-formed numeric response is deliberately recorded as
            # incorrect; an unsupported/ambiguous response remains flagged.
            now = utc_now()
            events = learner_events.events_from_assessment(item, response_obj, spec, now=now)
            state_path = root / "curriculum" / f"{subject}.learnerstate.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_store = learner_state.LearnerStateStore(str(state_path), spec)
            state_store.record_events(events, now=now)
            state = state_store.state()
            state_dict = state.to_dict() if state is not None else None
            if state_dict is not None:
                db.put_subject_state(subject, state_dict)
            public_grade = dict(grade)
            if isinstance(public_grade.get("answer_key"), dict):
                public_key = dict(public_grade["answer_key"])
                public_key.pop("numeric_key", None)
                public_grade["answer_key"] = public_key
            result = {"grade": public_grade,
                      "state": _state_view(spec, state_dict or db.get_subject_state(subject))}
            db.put_assessment_result(subject, body.item_id, result)
            return result

    @app.get("/api/settings")
    def settings():
        settings = db.get_settings()
        return {
            "base_url": settings.get("base_url"),
            "model": settings.get("model"),
            "mode": settings.get("mode"),
            "configured": bool(settings.get("base_url") and settings.get("model")),
        }

    @app.put("/api/settings")
    def update_settings(body: SettingsRequest, _: None = Depends(write_guard)):
        if body.mode is not None and body.mode not in {"extractive", "local-model"}:
            raise HTTPException(status_code=400, detail="mode must be extractive or local-model")
        if body.base_url is not None and not local_endpoint(body.base_url):
            raise HTTPException(
                status_code=400, detail="base_url must be a loopback HTTP(S) endpoint"
            )
        values = body.model_dump(exclude_unset=True)
        updated = db.update_settings(values)
        return {
            "base_url": updated.get("base_url"),
            "model": updated.get("model"),
            "mode": updated.get("mode"),
            "configured": bool(updated.get("base_url") and updated.get("model")),
        }

    # The fallback is intentionally last and never handles /api paths.
    static_root = (
        Path(frontend_dir) if frontend_dir else Path(__file__).parent.parent / "web" / "dist"
    )
    if static_root.is_dir():

        @app.get("/{path:path}")
        async def spa(path: str):
            if path.startswith("api/") or ".." in Path(path).parts:
                raise HTTPException(status_code=404, detail="not found")
            requested = (static_root / path).resolve()
            if requested.is_file() and static_root.resolve() in requested.parents:
                return FileResponse(requested)
            index = static_root / "index.html"
            if index.is_file():
                return FileResponse(index)
            raise HTTPException(status_code=404, detail="not found")

    return app
