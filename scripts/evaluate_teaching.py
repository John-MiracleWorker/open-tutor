#!/usr/bin/env python3
"""Live local-model acceptance probes; isolated loopback QA server only.

No mocked completions or invented metrics. Requests, durable results, timings
and invariant checks are recorded before aggregation. Does not score pedagogy.
"""
from __future__ import annotations
import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

CASES = [
    ("quantum-start", "quantum-computing", "qubit", "respond", "plain", "Teach me what a qubit is as a complete beginner. One idea and one question."),
    ("quantum-confused", "quantum-computing", "qubit", "confused", None, "I'm still lost. I don't understand what a state means."),
    ("quantum-visual", "quantum-computing", "qubit", "visual", None, "Map this idea visually so I can explore the connections."),
    ("quantum-response", "quantum-computing", "qubit", "respond", None, "I think it means the qubit secretly already has either 0 or 1 and we just don't know which."),
    ("quantum-hint", "quantum-computing", "qubit", "hint", None, "A small hint for your question please, not the answer."),
    ("quantum-another", "quantum-computing", "qubit", "another-way", None, "Please try a different teaching approach."),
    ("quantum-transfer", "quantum-computing", "qubit", "got-it", None, "That clicked. Let's see if I can use it."),
    ("calculus-example", "calculus", "derivative", "example", None, "Walk me through how a derivative describes change. Use a simple worked example."),
    ("history-analogy", "history", "primary-source", "respond", "analogy", "Explain primary versus secondary historical sources using a familiar analogy, including its limits."),
    ("music-analogy", "music-theory", "major-scale", "respond", "analogy", "Explain a major scale with a familiar analogy. Tell me where the analogy stops working."),
]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:9140")
    ap.add_argument("--out", default="out/adaptive-qa/live")
    ap.add_argument("--only", help="Comma-separated case IDs")
    args = ap.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port == 9130:
        ap.error("Only a non-production loopback QA server is permitted")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, body=None, method=None):
        req = urllib.request.Request(args.base_url + "/api" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method=method or ("GET" if body is None else "POST"))
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    settings = request("/settings")
    if not settings.get("base_url") or not settings.get("model"):
        raise RuntimeError("Configure the QA local model before evaluation")
    chosen = [c for c in CASES if args.only is None or c[0] in args.only.split(",")]
    if not chosen:
        raise RuntimeError("No matching scenarios")
    threads = {}
    records = []
    for case, subject, node, action, approach, question in chosen:
        detail = request("/subjects/" + subject)
        nodes = [n["id"] for n in detail["spec"]["nodes"]]
        if node not in nodes:
            raise RuntimeError(f"Scenario {case} requests {node}, but actual nodes are {nodes}")
        if subject not in threads:
            threads[subject] = request("/threads", {"subject": subject})["id"]
        thread = threads[subject]
        before = request("/threads/" + thread).get("teaching_state", {})
        mastery_before = {n: v["mastery"] for n, v in detail["state"]["nodes"].items()}
        payload = {"question": question, "node_id": node, "teaching": True, "action": action}
        if approach:
            payload["approach"] = approach
        start = time.monotonic()
        job_id = request("/threads/" + thread + "/ask", payload)["job_id"]
        deadline = start + 180
        while True:
            job = request("/jobs/" + job_id)
            if job["status"] not in {"queued", "running"}:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Case {case}: durable job {job_id} exceeds 180s")
            time.sleep(.35)
        elapsed = round(time.monotonic() - start, 3)
        readback = request("/threads/" + thread)
        answer = job.get("result") or {}
        teaching = answer.get("teaching") or {}
        after_detail = request("/subjects/" + subject)
        mastery_after = {n: v["mastery"] for n, v in after_detail["state"]["nodes"].items()}
        checks = {
            "completed": job["status"] == "completed",
            "ready": teaching.get("status") == "ready",
            "unverified_coaching": answer.get("grounded") is False and teaching.get("verified") is False,
            "separate_evidence": answer.get("evidence_grounded") is True and answer.get("verification_scope") == "evidence-only",
            "activity": isinstance(teaching.get("activity"), dict),
            "persisted_exact": bool(readback["messages"]) and readback["messages"][-1].get("answer") == answer,
            "no_mastery_award": mastery_before == mastery_after,
        }
        if action == "visual":
            checks["visual_diagram"] = teaching.get("approach") == "visual" and bool(teaching.get("diagram"))
        if action == "another-way":
            checks["changed_approach"] = teaching.get("approach") != before.get("approach")
        if action == "confused":
            checks["gentle_scaffold"] = teaching.get("pace") == "gentle" and teaching.get("intent") == "scaffold"
        if action == "hint":
            checks["hint_ramp"] = readback.get("teaching_state", {}).get("hint_level", 0) > before.get("hint_level", 0)
        record = {"case": case, "subject": subject, "thread_id": thread, "job_id": job_id,
            "request": payload, "elapsed_s": elapsed, "checks": checks, "job": job, "teaching_state": readback.get("teaching_state")}
        (out / (case + ".json")).write_text(json.dumps(record, indent=2, ensure_ascii=False))
        with (out / "results.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        records.append(record)
        print(json.dumps({"case": case, "seconds": elapsed, "status": teaching.get("status"), "checks": checks}), flush=True)
    unique = {r["case"]: r for r in records}
    assert len(unique) == len(chosen)
    summary = {"model": settings["model"], "count": len(unique), "passed": sum(all(r["checks"].values()) for r in unique.values()),
        "median_s": statistics.median(r["elapsed_s"] for r in unique.values()), "max_s": max(r["elapsed_s"] for r in unique.values()),
        "threads": threads, "case_ids": sorted(unique)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if summary["passed"] != summary["count"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
