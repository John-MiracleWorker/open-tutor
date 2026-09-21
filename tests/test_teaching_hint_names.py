"""Composed-hint source-name display regressions found in the live QA run."""
from open_tutor.teaching import TeachingState, compose_hint, select_plan

PENDING = "In one sentence, distinguish the height f(3) from the slope f'(3) for x^2."
SAME_SOURCE = [
    {"ref": 1, "source_name": "Wikipedia: Qubit (concept reference)", "text": "A qubit is a basic unit. " * 8},
    {"ref": 2, "source_name": "Wikipedia: Qubit (concept reference)", "text": "Different passage. " * 8},
    {"ref": 3, "source_name": "Wikipedia: Qubit (concept reference)", "text": "Third passage. " * 8},
]


def _hint(prior_refs):
    plan = select_plan("A hint", action="hint",
                       state=TeachingState(node_id="n", pending_question=PENDING))
    return compose_hint(plan=plan, pending_question=PENDING,
                        evidence=SAME_SOURCE, prior_refs=prior_refs)


def test_repeated_source_name_is_listed_once():
    explanation = _hint([1, 2, 3])["explanation"]
    assert explanation.count("Wikipedia: Qubit (concept reference)") == 1


def test_distinct_source_names_are_all_kept():
    distinct = [dict(item, source_name=f"Source {item['ref']}") for item in SAME_SOURCE]
    plan = select_plan("A hint", action="hint",
                       state=TeachingState(node_id="n", pending_question=PENDING))
    explanation = compose_hint(plan=plan, pending_question=PENDING,
                               evidence=distinct, prior_refs=[1, 2, 3])["explanation"]
    assert explanation.count("Source 1 and Source 2 and Source 3") == 1