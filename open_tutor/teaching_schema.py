"""Structural (not factual) JSON grammar for local teaching output."""
from __future__ import annotations

from typing import Any


def teaching_schema(approach: str) -> dict[str, Any]:
    def text(limit: int):
        # Keep provider constraints structural. llama.cpp b10453 compiles a
        # negated-class pattern without JSON string escaping or length bounds:
        # ^[^?？]*$ can swallow closing quotes and the rest of the object.
        # validate_teaching_output independently enforces the question policy.
        return {"type": "string", "minLength": 1, "maxLength": limit}
    def obj(properties: dict):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    step = obj({"title": text(80), "body": text(500)})
    node = obj({"id": text(30), "label": text(70), "detail": text(180)})
    edge = obj({"from": text(30), "to": text(30), "label": text(60)})
    # These bounds must agree with validate_teaching_output and the prompt
    # text ("map 2-6 nodes, at most 10 edges").  A tighter grammar here forced
    # the model to drop nodes its edges still referenced (live failure).
    diagram = obj({"title": text(100), "nodes": {"type": "array", "items": node, "minItems": 2, "maxItems": 6},
        "edges": {"type": "array", "items": edge, "minItems": 1, "maxItems": 10}})
    return obj({"title": text(100), "explanation": text(1400),
        "steps": {"type": "array", "items": step, "minItems": 2 if approach == "worked-example" else 0, "maxItems": 3 if approach == "worked-example" else 0},
        "diagram": diagram if approach == "visual" else {"type": "null"},
        "activity": obj({"kind": {"type": "string", "enum": ["predict", "explain", "apply", "reflect"]}, "prompt": text(350)}),
        "evidence_refs": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}, "maxItems": 5}})
