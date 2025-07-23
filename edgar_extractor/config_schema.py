# edgar_extractor/config_schema.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union


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
    required_dims: Dict[str, Union[str, List[str]]] = field(default_factory=dict)
    units: Optional[List[str]] = None
    period_type: Optional[str] = None          # "duration" | "instant"
    category: Optional[str] = None             # e.g. "balance_sheet.assets"
    filter_for_consolidated: bool = False      # NEW: enforce consolidated-members filter


@dataclass
class SegmentRule:
    name: str
    concept: str
    required_dims: Dict[str, Union[str, List[str]]]
    units: Optional[List[str]] = None
    period_type: Optional[str] = None
    strategy: MetricStrategy = MetricStrategy.SUM


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
