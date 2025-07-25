import types
from edgar_extractor.metrics import _dims_match, _unit_match, _period_type_match, _is_consolidated, _period_type_of_fact

def make_fact(value=1.0, unit="USD", period_key=("", "2024-12-31"), dims=None):
    f = types.SimpleNamespace()
    f.value = value
    f.unit = unit
    f.period_key = period_key
    f.dims = dims or {}
    return f

def test_dims_match():
    f = make_fact(dims={"axis": "member"})
    assert _dims_match(f, {"axis": "member"}, {})
    assert not _dims_match(f, {"axis": "other"}, {})
    
    # Test None allows any dimensions, empty dict only allows pure facts
    assert _dims_match(f, None, {})
    assert not _dims_match(f, {}, {})  # f has dims, so {} should reject it
    
    # Test pure fact (no dims) with empty dict requirement
    f_pure = make_fact(dims={})
    assert _dims_match(f_pure, {}, {})
    
    # Test axis aliases
    f_with_alias = make_fact(dims={"us-gaap:StatementBusinessSegmentsAxis": "member"})
    axis_aliases = {"segment": ["us-gaap:StatementBusinessSegmentsAxis"]}
    assert _dims_match(f_with_alias, {"segment": "member"}, axis_aliases)

def test_unit_match():
    f = make_fact(unit="USD")
    assert _unit_match(f, ["USD"])
    assert not _unit_match(f, ["EUR"])
    
    # Test None units (no constraint)
    assert _unit_match(f, None)

def test_consolidated_filtering():
    # Test fact with no dimensions is considered consolidated
    f_no_dims = make_fact(dims={})
    assert _is_consolidated(f_no_dims, ["ConsolidatedMember"])
    
    # Test fact with consolidated member
    f_consolidated = make_fact(dims={"axis": "ConsolidatedMember"})
    assert _is_consolidated(f_consolidated, ["ConsolidatedMember"])
    
    # Test fact with non-consolidated member
    f_segment = make_fact(dims={"axis": "SegmentMember"})
    assert not _is_consolidated(f_segment, ["ConsolidatedMember"])
    
    # Test multiple consolidated members
    f_other_consolidated = make_fact(dims={"axis": "TotalMember"})
    assert _is_consolidated(f_other_consolidated, ["ConsolidatedMember", "TotalMember"])

def test_axis_aliases_comprehensive():
    # Test direct match (no alias needed)
    f_direct = make_fact(dims={"segment": "ProductA"})
    assert _dims_match(f_direct, {"segment": "ProductA"}, {})
    
    # Test alias match
    f_alias = make_fact(dims={"us-gaap:StatementBusinessSegmentsAxis": "ProductA"})
    axis_aliases = {"segment": ["us-gaap:StatementBusinessSegmentsAxis"]}
    assert _dims_match(f_alias, {"segment": "ProductA"}, axis_aliases)
    
    # Test multiple aliases
    f_alias2 = make_fact(dims={"pri:SegmentAxis": "ProductA"})
    axis_aliases_multi = {"segment": ["us-gaap:StatementBusinessSegmentsAxis", "pri:SegmentAxis"]}
    assert _dims_match(f_alias2, {"segment": "ProductA"}, axis_aliases_multi)
    
    # Test missing alias fails
    f_wrong = make_fact(dims={"wrong:Axis": "ProductA"})
    assert not _dims_match(f_wrong, {"segment": "ProductA"}, axis_aliases)
    
    # Test multiple required dimensions with aliases
    f_multi = make_fact(dims={
        "us-gaap:StatementBusinessSegmentsAxis": "ProductA",
        "us-gaap:ProductOrServiceAxis": "ServiceType1"
    })
    multi_aliases = {
        "segment": ["us-gaap:StatementBusinessSegmentsAxis"],
        "product": ["us-gaap:ProductOrServiceAxis"]
    }
    assert _dims_match(f_multi, {"segment": "ProductA", "product": "ServiceType1"}, multi_aliases)

def test_flexible_dimension_matching():
    # Test that facts can have additional dimensions beyond required ones
    f_extra = make_fact(dims={
        "segment": "ProductA", 
        "region": "US", 
        "currency": "USD"
    })
    # Only require segment, should match even with extra dims
    assert _dims_match(f_extra, {"segment": "ProductA"}, {})
    
    # Test list of acceptable values
    assert _dims_match(f_extra, {"segment": ["ProductA", "ProductB"]}, {})
    assert not _dims_match(f_extra, {"segment": ["ProductB", "ProductC"]}, {})

def test_empty_dict_vs_none_semantics():
    """Test the critical difference between {} and None for required_dims"""
    # Fact with dimensions (like a segment fact)
    f_with_dims = make_fact(dims={"us-gaap:StatementBusinessSegmentsAxis": "SegmentA"})
    
    # Fact without dimensions (like a consolidated fact)
    f_pure = make_fact(dims={})
    
    # None means "no constraint" - accept any dimensions
    assert _dims_match(f_with_dims, None, {})
    assert _dims_match(f_pure, None, {})
    
    # {} means "only pure facts, no dimensions allowed"
    assert not _dims_match(f_with_dims, {}, {})  # Reject segmented fact
    assert _dims_match(f_pure, {}, {})           # Accept pure fact
    
    # This is critical for revenues rule which uses {} to reject segmented data

def test_period_type_match():
    """Test period type matching for duration vs instant"""
    # Duration fact (has start and end dates)
    f_duration = make_fact(period_key=("2024-01-01", "2024-12-31"))
    assert _period_type_of_fact(f_duration) == "duration"
    assert _period_type_match(f_duration, "duration")
    assert not _period_type_match(f_duration, "instant")
    
    # Instant fact (only end date, no start)
    f_instant = make_fact(period_key=("", "2024-12-31"))
    assert _period_type_of_fact(f_instant) == "instant"
    assert _period_type_match(f_instant, "instant")
    assert not _period_type_match(f_instant, "duration")
    
    # None period type means no constraint
    assert _period_type_match(f_duration, None)
    assert _period_type_match(f_instant, None)
