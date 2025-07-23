"""Top-level package exports"""

from .config_schema import (
    SegmentRule,
    CompanyConfig,
    GlobalConfig,
    MetricRule,
    MetricStrategy,
)
from .xbrl_index import XBRLIndex, Fact
from .metrics import extract_all
from .utils import load_company_config
from .sec_client import SECClient

__all__ = [
    "SegmentRule",
    "CompanyConfig",
    "GlobalConfig",
    "MetricRule",
    "MetricStrategy",
    "XBRLIndex",
    "Fact",
    "extract_all",
    "load_company_config",
    "SECClient",
]
