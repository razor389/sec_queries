# edgar_extractor/config_schema.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union


def year_matches_range(year: int, year_range: Optional[str]) -> bool:
    """
    Check if a year falls within a specified range.
    
    Args:
        year: The year to check (e.g., 2022)
        year_range: Range string like "2020-2024", "2018-2021", or None for all years
    
    Returns:
        True if year is in range or no range specified
    """
    if not year_range:
        return True
    
    if '-' not in year_range:
        # Single year
        return year == int(year_range)
    
    start_year, end_year = year_range.split('-')
    return int(start_year) <= year <= int(end_year)


class MetricStrategy(str, Enum):
    PICK_FIRST = "pick_first"
    SUM = "sum"
    LATEST_IN_YEAR = "latest_in_year"
    MAX = "max"
    MIN = "min"
    AVG = "avg"


@dataclass
class MetricRule:
    name: str
    aliases: List[str]
    strategy: MetricStrategy = MetricStrategy.PICK_FIRST
    required_dims: Optional[Dict[str, Union[str, List[str]]]] = None
    units: Optional[List[str]] = None
    period_type: Optional[str] = None          # "duration" | "instant"
    category: Optional[str] = None             # e.g. "balance_sheet.assets"
    filter_for_consolidated: bool = False      # NEW: enforce consolidated-members filter
    years: Optional[str] = None                # e.g. "2020-2024" or "2018-2021"


@dataclass
class SegmentRule:
    name: str
    concept: str
    required_dims: Optional[Dict[str, Union[str, List[str]]]] = None
    units: Optional[List[str]] = None
    period_type: Optional[str] = None
    strategy: MetricStrategy = MetricStrategy.PICK_FIRST
    filter_for_consolidated: bool = False      # NEW: enforce consolidated-members filter
    years: Optional[str] = None                # e.g. "2020-2024" or "2018-2021"


@dataclass
class CompanyConfig:
    concept_aliases: Dict[str, List[str]] = field(default_factory=dict)
    axis_aliases: Dict[str, List[str]] = field(default_factory=dict)
    consolidated_members: List[str] = field(default_factory=list)

    metrics: List[MetricRule] = field(default_factory=list)
    segments: List[SegmentRule] = field(default_factory=list)

    balance_sheet_concepts: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class GlobalConfig:
    default: CompanyConfig
    companies: Dict[str, CompanyConfig]
