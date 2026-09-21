"""Provider grammar stays structural; host teaching policy stays independent."""
import json

import pytest

from open_tutor.teaching import validate_teaching_output
from open_tutor.teaching_schema import teaching_schema


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


@pytest.mark.parametrize("approach", ["plain", "visual", "worked-example", "analogy", "socratic", "challenge"])
def test_provider_uses_bounded_json_strings_not_unsafe_regex_patterns(approach):
    # llama.cpp b10453 compiles pattern directly, without intersecting JSON's
    # escaped-string grammar. ^[^?？]*$ admits quotes/newlines and absorbs the
    # rest of the object, so it can never reach EOS despite valid-looking JSON.
    strings = [node for node in _nodes(teaching_schema(approach)) if node.get("type") == "string" and "enum" not in node]
    assert strings
    for node in strings:
        assert "pattern" not in node
        assert node["minLength"] == 1
        assert 1 <= node["maxLength"] <= 1400


@pytest.mark.parametrize("field", ["title", "explanation"])
@pytest.mark.parametrize("mark", ["?", "？"])
def test_question_policy_remains_host_enforced(field, mark):
    output = {"title": "A concept", "explanation": "A short explanation.", "steps": [], "diagram": None,
              "activity": {"kind": "predict", "prompt": "What happens?"}, "evidence_refs": [1]}
    output[field] += mark
    with pytest.raises(ValueError, match="only learner question"):
        validate_teaching_output(json.dumps(output), known_refs={1})


@pytest.mark.parametrize("field", ["title", "node-id", "node-label", "node-detail", "edge-label"])
@pytest.mark.parametrize("mark", ["?", "？"])
def test_question_policy_covers_every_diagram_string(field, mark):
    diagram = {"title": "Map", "nodes": [
        {"id": "a", "label": "A", "detail": "First"},
        {"id": "b", "label": "B", "detail": "Second"}],
        "edges": [{"from": "a", "to": "b", "label": "connects"}]}
    if field == "title":
        diagram["title"] += mark
    elif field == "edge-label":
        diagram["edges"][0]["label"] += mark
    else:
        key = field.removeprefix("node-")
        diagram["nodes"][0][key] += mark
        if key == "id":
            diagram["edges"][0]["from"] = diagram["nodes"][0]["id"]
    output = {"title": "Concept", "explanation": "An explanation.", "steps": [], "diagram": diagram,
              "activity": {"kind": "predict", "prompt": "What happens?"}, "evidence_refs": [1]}
    with pytest.raises(ValueError, match="only learner question"):
        validate_teaching_output(json.dumps(output), known_refs={1})
