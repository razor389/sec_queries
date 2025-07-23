from edgar_extractor.metrics import Accumulator
from edgar_extractor.config_schema import MetricStrategy

def test_accumulator_basic():
    acc = Accumulator()
    acc.update(10, "2024-01-01")
    acc.update(5, "2024-02-01")
    assert acc.count == 2
    assert acc.total == 15
    assert acc.max_val == 10
    assert acc.min_val == 5
    assert acc.latest_value == 5

    assert acc.result(MetricStrategy.SUM) == 15
    assert acc.result(MetricStrategy.AVG) == 7.5
    assert acc.result(MetricStrategy.MAX) == 10
    assert acc.result(MetricStrategy.MIN) == 5
    assert acc.result(MetricStrategy.LATEST_IN_YEAR) == 5
