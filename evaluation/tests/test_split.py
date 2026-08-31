from evaluation.generators.build_dataset import build_dataset
from evaluation.generators.split import held_out_split


def test_split_covers_the_whole_dataset_with_no_overlap():
    dataset = build_dataset(count=300, seed=11)
    development, held_out = held_out_split(dataset, 0.2)

    dev_ids = {case.input.case_id for case in development}
    held_out_ids = {case.input.case_id for case in held_out}

    assert dev_ids.isdisjoint(held_out_ids)
    assert len(dev_ids) + len(held_out_ids) == dataset.count


def test_split_fraction_is_approximately_respected():
    dataset = build_dataset(count=1000, seed=11)
    _development, held_out = held_out_split(dataset, 0.2)

    fraction = len(held_out) / dataset.count
    assert abs(fraction - 0.2) < 0.05


def test_split_is_deterministic_for_same_seed_and_fraction():
    dataset = build_dataset(count=300, seed=11)
    _dev_a, held_out_a = held_out_split(dataset, 0.2)
    _dev_b, held_out_b = held_out_split(dataset, 0.2)

    assert [c.input.case_id for c in held_out_a] == [c.input.case_id for c in held_out_b]


def test_split_assignment_depends_only_on_case_id_not_dataset_size():
    # Regenerating with a larger count for the same seed shouldn't move a
    # case that appears in both datasets from one bucket to the other.
    small = build_dataset(count=200, seed=11)
    large = build_dataset(count=600, seed=11)

    _small_dev, small_held_out = held_out_split(small, 0.2)
    _large_dev, large_held_out = held_out_split(large, 0.2)

    small_ids = {c.input.case_id for c in small.cases}
    large_ids = {c.input.case_id for c in large.cases}
    shared_ids = small_ids & large_ids
    assert shared_ids  # sanity: scenario_* naming means there should be overlap

    small_held_out_ids = {c.input.case_id for c in small_held_out}
    large_held_out_ids = {c.input.case_id for c in large_held_out}
    for case_id in shared_ids:
        assert (case_id in small_held_out_ids) == (case_id in large_held_out_ids)


def test_rejects_invalid_holdout_fraction():
    dataset = build_dataset(count=50, seed=1)
    try:
        held_out_split(dataset, 0.0)
        raised = False
    except ValueError:
        raised = True
    assert raised
