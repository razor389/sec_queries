import types
from edgar_extractor.metrics import _dims_match, _unit_match, _period_type_match

def make_fact(value=1.0, unit="USD", period_key=("", "2024-12-31"), dims=None):
    f = types.SimpleNamespace()
    f.value = value
    f.unit = unit
    f.period_key = period_key
    f.dims = dims or {}
    return f

def test_dims_match():
    f = make_fact(dims={"axis": "member"})
    assert _dims_match(f, {"axis": "member"})
    assert not _dims_match(f, {"axis": "other"})

def test_unit_match():
    f = make_fact(unit="USD")
    assert _unit_match(f, ["USD"])
    assert not _unit_match(f, ["EUR"])
