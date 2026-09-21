"""The model may cite only integer references actually present in its prompt."""
import json

import pytest

from open_tutor.llm import LocalCompletionError
from open_tutor.teaching import generate_teaching, select_plan


@pytest.mark.parametrize("evidence,ref", [
    ([{"ref": n, "text": f"Source {n}"} for n in range(1, 7)], 6),
    ([{"ref": True, "text": "A boolean is not reference 1."}], 1),
])
def test_refs_outside_serialized_source_data_are_rejected(evidence, ref):
    calls = []
    def completion(messages):
        calls.append(messages)
        return json.dumps({"title": "One idea", "explanation": "A brief explanation.",
                           "steps": [], "diagram": None,
                           "activity": {"kind": "explain", "prompt": "Explain the idea."},
                           "evidence_refs": [ref]})
    with pytest.raises(LocalCompletionError, match="evidence reference is unknown"):
        generate_teaching(plan=select_plan("Teach"), history=[], pending_question=None,
                          request="Teach", evidence=evidence, oracle={}, profile={},
                          base_url="http://localhost:8081", model="test", completion_fn=completion)
    assert len(calls) == 2
