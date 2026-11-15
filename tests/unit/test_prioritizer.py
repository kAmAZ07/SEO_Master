"""DEC-04 formula, explicit unknowns, stable triage and manual overrides."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.management_service.priority_policy import (
    PriorityChange, PriorityInputs, PriorityRecord, calculate_priority, load_priority, priority_key,
)


def test_formula_and_effort_monotonicity():
    assert calculate_priority(.8, .75, 2) == pytest.approx(.3)
    assert calculate_priority(.8, .75, 4) == pytest.approx(.15)
    assert calculate_priority(0., 0., 1) == 0


def test_orm_delegates_to_same_formula_and_ignores_legacy_defaults():
    from services.management_service.models import Task
    inputs = PriorityInputs(impact=.8, confidence=.75, effort=2.)
    row = Task(impact_score=.8, effort_score=.5, meta={'priority': PriorityRecord(inputs=inputs).model_dump(mode='json')})
    assert row.calculate_priority() == pytest.approx(.3)
    row.meta = {'expected_ffscore': 100}
    assert row.calculate_priority() is None


@pytest.mark.parametrize("values", [(None, .8, 1), (.8, None, 1), (.8, .8, None), (None, None, None)])
def test_missing_is_unknown_and_never_imputed(values):
    assert calculate_priority(*values) is None


@pytest.mark.parametrize("field,value", [
    ("impact", True), ("impact", -.1), ("impact", 1.1), ("confidence", "0.5"),
    ("confidence", float('nan')), ("effort", float('inf')), ("effort", .5), ("effort", 6),
])
def test_invalid_inputs_fail_without_clamping(field, value):
    with pytest.raises(ValidationError):
        PriorityInputs.model_validate({field: value})


def test_urgency_needs_reason_and_is_separate_from_score():
    with pytest.raises(ValidationError):
        PriorityInputs(urgency='critical')
    inputs = PriorityInputs(impact=.8, confidence=.75, effort=2, urgency='critical', urgency_reason='Site unavailable')
    view = PriorityRecord(inputs=inputs).snapshot()
    assert view['computed_score'] == pytest.approx(.3)
    assert view['urgency_rank'] == 0 and view['heuristic'] is True


def task(number, *, impact=None, urgency='unknown'):
    inputs = PriorityInputs(impact=impact, confidence=1., effort=1., urgency=urgency,
                            urgency_reason='Triage' if urgency != 'unknown' else None)
    return SimpleNamespace(id=UUID(int=number), created_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
                           meta={'priority': PriorityRecord(inputs=inputs).model_dump(mode='json')})


def test_sort_urgency_then_known_score_then_oldest_then_uuid():
    unknown, zero, high, urgent = task(1), task(2, impact=0.), task(3, impact=.9), task(4, urgency='critical')
    assert sorted([unknown, zero, high, urgent], key=priority_key) == [urgent, high, zero, unknown]
    a, b = task(8, impact=.5), task(9, impact=.5)
    assert sorted([b, a], key=priority_key) == [a, b]
    b.created_at = datetime(2026, 11, 14, tzinfo=timezone.utc)
    assert sorted([a, b], key=priority_key) == [b, a]


def test_legacy_fabricated_scores_are_not_canonical_inputs():
    view = load_priority({'current_ffscore': 50, 'expected_ffscore': 100, 'impact': .9}).snapshot()
    assert view['effective_score'] is None and view['inputs']['impact'] is None
    assert view['status'] == 'unknown' and view['revision'] == 0


@pytest.mark.parametrize('reason', ['', '  ', 'bad\x00reason', '\u044f' * 513])
def test_override_reason_is_required_bounded_and_inert(reason):
    with pytest.raises(ValidationError):
        PriorityChange(action='override', expected_revision=0, score=0., reason=reason)


def test_commands_cannot_forge_actor_or_mix_estimates_with_override():
    for extra in ({'actor_id': 'forged'}, {'inputs': {'impact': .5}}, {'score': None}):
        payload = dict(action='override', expected_revision=0, score=.4, reason='Manual triage')
        payload.update(extra)
        with pytest.raises(ValidationError):
            PriorityChange.model_validate(payload)
