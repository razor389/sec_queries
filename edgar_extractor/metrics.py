# edgar_extractor/metrics.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, List, Tuple

from .config_schema import CompanyConfig, MetricRule, MetricStrategy, SegmentRule
from .xbrl_index import Fact, XBRLIndex


# ------------------------- Small utilities ------------------------- #

def _year_of_fact(f: Fact) -> str:
    start, end = f.period_key
    date = end or start
    return date[:4] if date else ""


def _period_type_of_fact(f: Fact) -> str:
    start, end = f.period_key
    return "instant" if not start and end else "duration"


def _dims_match(f: Fact, required: Dict[str, Any] | None) -> bool:
    if not required:
        return True
    for axis, expected in required.items():
        exp_list = expected if isinstance(expected, list) else [expected]
        if axis not in f.dims or f.dims[axis] not in exp_list:
            return False
    return True


def _unit_match(f: Fact, units: List[str] | None) -> bool:
    if not units:
        return True
    return f.unit in units


def _period_type_match(f: Fact, ptype: str | None) -> bool:
    if not ptype:
        return True
    return _period_type_of_fact(f) == ptype


def _is_consolidated(f: Fact, consolidated_members: List[str]) -> bool:
    # If no dims at all, assume it's consolidated
    if not f.dims:
        return True
    return any(m in f.dims.values() for m in consolidated_members)


def _place_value(results: Dict[str, dict], year: str, rule: MetricRule, name: str, value: float) -> None:
    """
    Centralized placement logic:
    - balance_sheet.<cat> goes under results[year]["balance_sheet"][cat][name]
    - everything else goes under results[year][name]
    """
    ydict = results.setdefault(year, {})
    if rule.category and rule.category.startswith("balance_sheet."):
        bs_cat = rule.category.split(".", 1)[1]
        bs_dict = ydict.setdefault("balance_sheet", {}).setdefault(bs_cat, {})
        bs_dict[name] = value
    else:
        ydict[name] = value


# ------------------------- Aggregation helper ------------------------- #

@dataclass
class Accumulator:
    count: int = 0
    total: float = 0.0
    max_val: float | None = None
    min_val: float | None = None
    latest_value: float | None = None
    latest_date: str | None = None

    def update(self, val: float, date: str):
        self.count += 1
        self.total += val
        self.max_val = val if self.max_val is None or val > self.max_val else self.max_val
        self.min_val = val if self.min_val is None or val < self.min_val else self.min_val
        if self.latest_date is None or date > self.latest_date:
            self.latest_date = date
            self.latest_value = val

    def result(self, strategy: MetricStrategy) -> float | None:
        if self.count == 0:
            return None
        if strategy == MetricStrategy.SUM:
            return self.total
        if strategy == MetricStrategy.AVG:
            return self.total / self.count
        if strategy == MetricStrategy.MAX:
            return self.max_val
        if strategy == MetricStrategy.MIN:
            return self.min_val
        if strategy == MetricStrategy.LATEST_IN_YEAR:
            return self.latest_value
        # PICK_FIRST is handled before accumulation; default fallback:
        return self.latest_value


# ------------------------- Core extraction ------------------------- #

def _build_concept_to_rules(cfg: CompanyConfig) -> Dict[str, List[MetricRule | SegmentRule]]:
    """
    Map each concept alias (and segment concept) to the rules that care about it
    so we don't scan cfg lists for every fact.
    """
    mapping: Dict[str, List[MetricRule | SegmentRule]] = {}
    for rule in cfg.metrics:
        for alias in rule.aliases:
            mapping.setdefault(alias, []).append(rule)
    for seg in cfg.segments:
        mapping.setdefault(seg.concept, []).append(seg)
    return mapping


def extract_all(index: XBRLIndex, cfg: CompanyConfig) -> Dict[str, dict]:
    """
    Single-pass extraction over all facts; supports multiple strategies and
    places results appropriately in one pass.

    Output example:
    {
      "2024": {
        "revenues": 123.0,
        "segments": {"personal_lines_agency": 10.0, ...},
        "balance_sheet": {
          "assets": {"us-gaap:Assets": 999.0},
          ...
        }
      },
      ...
    }
    """
    results: Dict[str, dict] = {}

    concept_to_rules = _build_concept_to_rules(cfg)

    # Accumulators for strategies other than PICK_FIRST
    metric_acc: DefaultDict[Tuple[str, str], Accumulator] = defaultdict(Accumulator)

    for f in index.facts.values():
        rules = concept_to_rules.get(f.concept)
        if not rules:
            continue

        year = _year_of_fact(f)
        if not year:
            continue

        date = f.period_key[1] or f.period_key[0] or ""

        for rule in rules:
            # Segment rules
            if isinstance(rule, SegmentRule):
                if (not _dims_match(f, rule.required_dims)
                        or not _unit_match(f, rule.units)
                        or not _period_type_match(f, rule.period_type)):
                    continue

                ydict = results.setdefault(year, {})
                segdict = ydict.setdefault("segments", {})
                segdict[rule.name] = segdict.get(rule.name, 0.0) + f.value
                continue

            # Metric rules
            if not _dims_match(f, rule.required_dims):
                continue
            if not _unit_match(f, rule.units):
                continue
            if not _period_type_match(f, rule.period_type):
                continue
            if rule.filter_for_consolidated and not _is_consolidated(f, cfg.consolidated_members):
                continue

            if rule.strategy == MetricStrategy.PICK_FIRST:
                # place only if not already present
                ydict = results.setdefault(year, {})
                already_set = False
                if rule.category and rule.category.startswith("balance_sheet."):
                    bs_cat = rule.category.split(".", 1)[1]
                    already_set = (
                        rule.name in ydict.get("balance_sheet", {})
                                           .get(bs_cat, {})
                    )
                else:
                    already_set = rule.name in ydict

                if not already_set:
                    _place_value(results, year, rule, rule.name, f.value)
                continue

            # For all other strategies, accumulate
            metric_acc[(year, rule.name)].update(f.value, date)

    # Flush accumulators
    for (year, name), acc in metric_acc.items():
        # Retrieve the rule (multiple metrics could share a name, but we assume unique names)
        rule = next(r for r in cfg.metrics if r.name == name)
        val = acc.result(rule.strategy)
        if val is None:
            continue
        _place_value(results, year, rule, name, val)

    return results
