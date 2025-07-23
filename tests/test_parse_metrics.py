from edgar_extractor.utils import _parse_metrics

def test_parse_metrics_backcompat():
    merged = {
        "concept_aliases": {"revenues": ["us-gaap:Revenues"]},
        "balance_sheet_concepts": {"assets": ["us-gaap:Assets"]}
    }
    rules = _parse_metrics(merged)
    names = {r.name for r in rules}
    assert "revenues" in names
    assert "us-gaap:Assets" in names
