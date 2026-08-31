from collections import Counter

from evaluation.generators.build_dataset import build_dataset
from evaluation.schemas.dataset_schema import ScenarioType


def test_dataset_has_requested_count():
    dataset = build_dataset(count=120, seed=1)
    assert dataset.count == 120
    assert len(dataset.cases) == 120


def test_same_seed_is_reproducible():
    a = build_dataset(count=200, seed=7)
    b = build_dataset(count=200, seed=7)
    assert [c.model_dump() for c in a.cases] == [c.model_dump() for c in b.cases]


def test_different_seed_generally_differs():
    a = build_dataset(count=200, seed=7)
    b = build_dataset(count=200, seed=8)
    assert [c.input.case_id for c in a.cases] != [c.input.case_id for c in b.cases]


def test_scenarios_are_roughly_evenly_distributed():
    dataset = build_dataset(count=600, seed=42)
    counts = Counter(case.input.scenario_type for case in dataset.cases)
    assert set(counts.keys()) == set(ScenarioType)
    expected_per_scenario = 600 / len(ScenarioType)
    for scenario_count in counts.values():
        assert abs(scenario_count - expected_per_scenario) <= 1


def test_repeated_failure_attempt_number_is_never_low():
    dataset = build_dataset(count=500, seed=42)
    repeated = [c for c in dataset.cases if c.input.scenario_type == ScenarioType.REPEATED_FAILURE]
    assert repeated
    for case in repeated:
        assert case.input.attempt_number >= 3
        # More attempts implies more elapsed time — never attempt 5 on day 0.
        assert case.input.days_since_first_attempt >= case.input.attempt_number


def test_previously_contacted_cases_are_at_the_contact_cap():
    dataset = build_dataset(count=500, seed=42)
    contacted = [c for c in dataset.cases if c.input.scenario_type == ScenarioType.PREVIOUSLY_CONTACTED]
    assert contacted
    for case in contacted:
        assert case.input.previous_contact_count >= 2


def test_high_value_cases_are_actually_high_value():
    dataset = build_dataset(count=500, seed=42)
    high_value = [c for c in dataset.cases if c.input.scenario_type == ScenarioType.HIGH_VALUE]
    assert high_value
    for case in high_value:
        assert case.input.is_high_value is True
        assert case.input.amount >= 500_000


def test_all_cases_have_a_ground_truth_rationale():
    dataset = build_dataset(count=100, seed=3)
    for case in dataset.cases:
        assert case.ground_truth.rationale.strip() != ""
