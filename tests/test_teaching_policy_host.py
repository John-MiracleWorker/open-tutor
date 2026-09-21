"""Host-enforced single-deliverable and edge-direction policy (RED before fixes).

Reproduced live defects:
- compound activities: one question mark but two coordinated deliverables
  ("what does state mean AND why is the analogy limited");
- backwards map edges: "State -- example of --> Light switch" and
  "Bit -- quantum version of --> Qubit";
- weak got-it transfer: the model re-asked the prior question.
"""
from __future__ import annotations

import json

import pytest

from open_tutor.teaching import (
    TeachingState,
    compose_transfer,
    generate_teaching,
    select_plan,
    validate_teaching_output,
)

EVIDENCE = [{"ref": 1, "source_name": "Qubit", "text": "A qubit holds quantum information. Superposition blends outcomes."}]


def _turn(activity_prompt, approach="plain", *, diagram=None):
    return {
        "title": "T", "explanation": "One idea.", "steps": [], "diagram": diagram,
        "activity": {"kind": "predict", "prompt": activity_prompt}, "evidence_refs": [1],
    }


BACKWARDS = {"title": "Map", "nodes": [
    {"id": "a", "label": "State", "detail": "The current condition of a thing."},
    {"id": "b", "label": "Light switch", "detail": "A simple object whose state can be on or off."}],
    "edges": [{"from": "a", "to": "b", "label": "example of"}]}

FORWARD = {"title": "Map", "nodes": BACKWARDS["nodes"],
           "edges": [{"from": "b", "to": "a", "label": "is an example of"}]}


def _five_concept_diagram():
    """The live failing shape: five concepts, all legitimately connected."""
    nodes = [{"id": n, "label": n.title(), "detail": f"{n} detail."}
             for n in ("qubit", "state", "probability", "measurement", "outcome")]
    edges = [
        {"from": "qubit", "to": "state", "label": "has"},
        {"from": "state", "to": "probability", "label": "sets"},
        {"from": "measurement", "to": "outcome", "label": "produces"},
        {"from": "probability", "to": "outcome", "label": "describes"},
        {"from": "state", "to": "outcome", "label": "is not the same as"},
    ]
    return {"title": "Qubit state map", "nodes": nodes, "edges": edges}


class RecordingCompletion:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, messages, *args, **kwargs):
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError("unexpected extra model call")
        return self.outputs.pop(0)


def test_compound_activity_is_rejected():
    compound = ("In your own words, what does a qubit's state mean, and then "
                "predict what a measurement would show?")
    with pytest.raises(ValueError, match="(?i)single"):
        validate_teaching_output(_turn(compound), known_refs={1})


def test_legitimate_single_question_passes():
    validate_teaching_output(
        _turn("In your own words, what does a qubit's state mean before measurement?"), known_refs={1})


def test_compound_connectors_in_activity_are_rejected():
    bad = [
        "What does state mean and why is the analogy limited?",
        "Predict the result, then explain the reason.",
        "Name the pattern. Also say where it stops.",
        "Say what happens and why.",
        "Explain the idea, then give an example.",
        "What does A mean and what does B mean?",
    ]
    for prompt in bad:
        with pytest.raises(ValueError, match="(?i)single|deliverable"):
            validate_teaching_output(_turn(prompt), known_refs={1})


def test_compound_word_pairs_are_not_false_positives():
    fine = [
        "Define superposition and interference.",
        "What does the passage say about bits and qubits?",
        "Explain why gates and measurements differ.",
        "What happens then?",
    ]
    for prompt in fine:
        validate_teaching_output(_turn(prompt), known_refs={1})


def test_repair_message_names_the_offending_path(monkeypatch):
    completion = RecordingCompletion([
        json.dumps(_turn("What does A mean and what does B mean?")),
        json.dumps(_turn("What does A mean?")),
    ])
    monkeypatch.setattr("open_tutor.teaching.local_completion", completion)
    plan = select_plan("Teach me qubits", action="respond", approach="plain",
                      state=TeachingState(node_id="qubit"))
    out = generate_teaching(plan=plan, history=[], pending_question=None,
                            request="Teach me qubits", evidence=EVIDENCE, oracle={},
                            profile={}, base_url="http://localhost:9120", model="test-qwen",
                            completion_fn=completion)
    assert out["activity"]["prompt"] == "What does A mean?"
    assert len(completion.calls) == 2  # first attempt + one repair
    repair_user = completion.calls[1][-1]["content"]
    assert "activity.prompt" in repair_user
    assert "single" in repair_user.lower()


def test_diagram_edge_reversal_is_caught():
    with pytest.raises(ValueError, match="(?i)edge|example"):
        validate_teaching_output(_turn("What is a state?", diagram=BACKWARDS), known_refs={1})


def test_diagram_edge_direction_hint_is_repairable(monkeypatch):
    completion = RecordingCompletion([
        json.dumps(_turn("What is a state?", approach="visual", diagram=BACKWARDS)),
        json.dumps(_turn("What is a state?", approach="visual", diagram=FORWARD)),
    ])
    monkeypatch.setattr("open_tutor.teaching.local_completion", completion)
    plan = select_plan("Map the qubit", action="visual", approach="visual",
                      state=TeachingState(node_id="qubit"))
    out = generate_teaching(plan=plan, history=[], pending_question=None,
                            request="Map the qubit", evidence=EVIDENCE, oracle={},
                            profile={}, base_url="http://localhost:9120", model="test-qwen",
                            completion_fn=completion)
    assert out["diagram"]["edges"][0]["from"] == "b"
    assert "edge" in completion.calls[1][-1]["content"].lower()


def test_bare_of_edge_labels_are_rejected():
    quantum_backwards = {"title": "Map", "nodes": [
        {"id": "bit", "label": "Bit", "detail": "A digital unit whose state is 0 or 1."},
        {"id": "qb", "label": "Qubit", "detail": "A quantum unit whose state can be a blend of 0 and 1."}],
        "edges": [{"from": "bit", "to": "qb", "label": "quantum version of"}]}
    with pytest.raises(ValueError, match="(?i)of|predicate"):
        validate_teaching_output(_turn("What is a qubit?", diagram=quantum_backwards), known_refs={1})
    quantum_forward = {"title": "Map", "nodes": quantum_backwards["nodes"],
                       "edges": [{"from": "qb", "to": "bit", "label": "is the quantum version of"}]}
    validate_teaching_output(_turn("What is a qubit?", diagram=quantum_forward), known_refs={1})


def test_five_concept_map_is_accepted_and_limits_agree():
    """The live quantum-visual failure: 5 concepts, 5 edges.

    The provider grammar capped nodes at 4 while the prompt said 6, so the
    model could not declare every concept its edges referenced.  Grammar,
    prompt and validator must agree, and a five-concept map must validate.
    """
    import inspect

    from open_tutor.teaching_schema import teaching_schema

    out = validate_teaching_output(_turn("What is a state?", diagram=_five_concept_diagram()), known_refs={1})
    assert len(out["diagram"]["nodes"]) == 5
    schema = teaching_schema("visual")
    diagram_schema = schema["properties"]["diagram"]
    assert diagram_schema["properties"]["nodes"]["maxItems"] == 6
    assert diagram_schema["properties"]["edges"]["maxItems"] == 10
    source = inspect.getsource(validate_teaching_output)
    assert "2 <= len(nodes) <= 6" in source
    assert "len(edges) > 10" in source


def test_transfer_turn_is_host_composed_no_model_call(monkeypatch):
    """got-it on a pending task must not call the model: the host composes a
    new-situation retrieval probe (no near-repeat, no answer giveaway)."""
    called = []

    def completion(messages, *args, **kwargs):
        called.append(messages)
        return "{}"

    monkeypatch.setattr("open_tutor.teaching.local_completion", completion)
    plan = select_plan("That clicked. New situation please.", action="got-it",
                       state=TeachingState(node_id="qubit",
                                           pending_question="What does a qubit's state mean?"))
    assert plan.next_state.last_action == "got-it"
    out = compose_transfer(plan=plan, pending_question="What does a qubit's state mean?",
                           evidence=EVIDENCE, prior_refs=[1])
    assert called == []
    assert out["activity"]["prompt"] != "What does a qubit's state mean?"  # NEW situation, not a repeat
    assert out["activity"]["prompt"].endswith("?")
    assert out["explanation"]
    assert "?" not in out["explanation"]
    # The composed probe must itself satisfy the strict model-facing rules.
    assert validate_teaching_output(out, known_refs={1})["activity"]["kind"] == "apply"
    assert called == []


def test_compose_transfer_requires_pending_question_and_got_it_action():
    with pytest.raises(ValueError):
        compose_transfer(plan=select_plan("x", action="got-it",
                                         state=TeachingState(node_id="qubit")),
                         pending_question="", evidence=EVIDENCE, prior_refs=[1])
    with pytest.raises(ValueError):
        compose_transfer(plan=select_plan("x", action="respond",
                                         state=TeachingState(node_id="qubit")),
                         pending_question="What does a qubit's state mean?",
                         evidence=EVIDENCE, prior_refs=[1])