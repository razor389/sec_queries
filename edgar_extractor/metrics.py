# edgar_extractor/metrics.py
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, List, Tuple

logger = logging.getLogger(__name__)

from .config_schema import CompanyConfig, MetricRule, MetricStrategy, SegmentRule, year_matches_range
from .xbrl_index import Fact, XBRLIndex


# ------------------------- Small utilities ------------------------- #

def _year_of_fact(f: Fact) -> str:
    start, end = f.period_key
    date = end or start
    return date[:4] if date else ""


def _period_type_of_fact(f: Fact) -> str:
    start, end = f.period_key
    return "instant" if not start and end else "duration"


def _dims_match(f: Fact, required: Dict[str, Any] | None, axis_aliases: Dict[str, List[str]] = None) -> bool:
    if required is None:
        return True          # no constraint - accept any dimensions
    if required == {}:
        return not f.dims    # only pure facts (no dimensions allowed)
    
    # A fact matches if it contains all required dimensions, even if it has additional ones
    # Support axis_aliases to map dimension names
    
    # Normalize axis_aliases to default empty dict
    if axis_aliases is None:
        axis_aliases = {}
    
    for axis, expected in required.items():
        exp_list = expected if isinstance(expected, list) else [expected]
        
        # Check if the axis exists directly in fact dims
        if axis in f.dims:
            if f.dims[axis] not in exp_list:
                logger.debug("    Axis '%s' has value '%s', expected one of %s", axis, f.dims[axis], exp_list)
                return False
            logger.debug("    Axis '%s' matches: '%s'", axis, f.dims[axis])
            continue
            
        # Check if any aliases for this axis exist in fact dims
        aliases = axis_aliases.get(axis, [])
        found_match = False
        for alias in aliases:
            if alias in f.dims:
                if f.dims[alias] not in exp_list:
                    logger.debug("    Alias axis '%s' (for '%s') has value '%s', expected one of %s", alias, axis, f.dims[alias], exp_list)
                    return False
                logger.debug("    Alias axis '%s' (for '%s') matches: '%s'", alias, axis, f.dims[alias])
                found_match = True
                break
        
        if not found_match:
            logger.debug("    Missing axis '%s' and no matching aliases %s in fact dims", axis, aliases)
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


def _report_missing_data(results: Dict[str, dict], cfg: CompanyConfig, target_years: List[int]) -> Dict[str, Dict[str, List[int]]]:
    """
    Analyze results to identify which categories are missing data for which years.
    
    Returns:
        Dict mapping category types to categories to missing years.
        Example: {
            "metrics": {"revenues": [2018, 2019]},
            "segments": {"term_life": [2020]},
            "balance_sheet": {"fixed_income": [2018, 2019, 2020]}
        }
    """
    missing = {"metrics": {}, "segments": {}, "balance_sheet": {}}
    
    # Check metrics
    for rule in cfg.metrics:
        if rule.category and rule.category.startswith("balance_sheet."):
            # Balance sheet metric
            bs_cat = rule.category.split(".", 1)[1]
            for year in target_years:
                # Only check years that this rule applies to
                if not year_matches_range(year, rule.years):
                    continue
                year_str = str(year)
                if (year_str not in results or 
                    "balance_sheet" not in results[year_str] or 
                    bs_cat not in results[year_str]["balance_sheet"] or
                    rule.name not in results[year_str]["balance_sheet"][bs_cat]):
                    missing["balance_sheet"].setdefault(bs_cat, []).append(year)
        else:
            # Regular metric
            for year in target_years:
                # Only check years that this rule applies to
                if not year_matches_range(year, rule.years):
                    continue
                year_str = str(year)
                if year_str not in results or rule.name not in results[year_str]:
                    missing["metrics"].setdefault(rule.name, []).append(year)
    
    # Check segments
    for rule in cfg.segments:
        for year in target_years:
            # Only check years that this rule applies to
            if not year_matches_range(year, rule.years):
                continue
            year_str = str(year)
            if (year_str not in results or 
                "segments" not in results[year_str] or 
                rule.name not in results[year_str]["segments"]):
                missing["segments"].setdefault(rule.name, []).append(year)
    
    # Remove empty categories and deduplicate
    for category in missing:
        for name in list(missing[category].keys()):
            missing[category][name] = sorted(list(set(missing[category][name])))
            if not missing[category][name]:
                del missing[category][name]
    
    return missing


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
    logger.info("Starting metric extraction with %d metric rules, %d segment rules", len(cfg.metrics), len(cfg.segments))
    results: Dict[str, dict] = {}

    concept_to_rules = _build_concept_to_rules(cfg)
    logger.debug("Built concept-to-rules mapping: %d concepts mapped", len(concept_to_rules))

    # Unified candidate collection for both metrics and segments
    candidate_facts: DefaultDict[Tuple[str, int], List[Fact]] = defaultdict(list)  # Key: (year, id(rule))
    rule_by_id = {}  # Map from id(rule) to rule object
    
    facts_processed = 0
    facts_matched = 0

    # Debug: Show all concepts that have segment rules
    segment_concepts = set()
    for seg in cfg.segments:
        segment_concepts.add(seg.concept)
    logger.debug("Segment concepts to look for: %s", segment_concepts)
    
    # Debug: Log some facts with dimensions to understand the data structure
    facts_with_dims_logged = 0
    
    for f in index.facts.values():
        facts_processed += 1
        
        # Debug: Show some facts that have dimensions
        if f.dims and facts_with_dims_logged < 10:
            logger.debug("Fact with dims: concept='%s', dims=%s, value=%s, year=%s", 
                        f.concept, f.dims, f.value, _year_of_fact(f))
            facts_with_dims_logged += 1
        
        rules = concept_to_rules.get(f.concept)
        if not rules:
            # Debug: Log if this is a segment concept we're missing
            if f.concept in segment_concepts:
                logger.debug("Found segment concept '%s' but no rules mapped: dims=%s, value=%s", 
                           f.concept, f.dims, f.value)
            continue
        facts_matched += 1
        
        # Debug: Log when we find facts for segment concepts
        if f.concept in segment_concepts:
            logger.debug("Processing fact for segment concept '%s': dims=%s, value=%s, year=%s", 
                        f.concept, f.dims, f.value, _year_of_fact(f))

        year = _year_of_fact(f)
        if not year:
            continue

        date = f.period_key[1] or f.period_key[0] or ""

        for rule in rules:
            # Track rule for later lookup
            rule_by_id[id(rule)] = rule
            
            # Check if rule applies to this year
            if not year_matches_range(int(year), getattr(rule, 'years', None)):
                logger.debug("Skipping rule '%s' - year %s not in range '%s'", rule.name, year, getattr(rule, 'years', 'all'))
                continue
                
            # Segment rules
            if isinstance(rule, SegmentRule):
                logger.debug("Processing segment rule '%s' for concept %s in year %s (range: %s)", 
                           rule.name, f.concept, year, getattr(rule, 'years', 'all'))
                logger.debug("  Fact dims: %s", f.dims)
                logger.debug("  Required dims: %s", rule.required_dims)
                logger.debug("  Fact value: %s, unit: %s, period_type: %s", f.value, f.unit, _period_type_of_fact(f))
                
                dims_match = _dims_match(f, rule.required_dims, cfg.axis_aliases)
                unit_match = _unit_match(f, rule.units)
                period_match = _period_type_match(f, rule.period_type)
                
                logger.debug("  Dims match: %s, Unit match: %s, Period match: %s", dims_match, unit_match, period_match)
                
                if not dims_match or not unit_match or not period_match:
                    logger.debug("  SKIPPED segment rule '%s' - failed matching criteria", rule.name)
                    continue

                # Apply consolidated filter if requested
                if rule.filter_for_consolidated and not _is_consolidated(f, cfg.consolidated_members):
                    logger.debug("  SKIPPED segment rule '%s' - failed consolidated filter", rule.name)
                    continue

                # Memory optimization: For PICK_FIRST strategy, only collect first matching fact
                key = (year, id(rule))
                if rule.strategy == MetricStrategy.PICK_FIRST:
                    if key not in candidate_facts:  # First match for this rule
                        candidate_facts[key].append(f)
                        logger.debug("  ADDED segment candidate '%s' (PICK_FIRST): value %s, dims=%s", rule.name, f.value, f.dims)
                    else:
                        logger.debug("  SKIPPED segment candidate '%s' - already have PICK_FIRST match", rule.name)
                else:
                    # For other strategies, collect all candidates
                    candidate_facts[key].append(f)
                    logger.debug("  ADDED segment candidate '%s': value %s, dims=%s", rule.name, f.value, f.dims)
                continue

            # Metric rules
            logger.debug("Processing metric rule '%s' for concept %s in year %s (range: %s)", 
                       rule.name, f.concept, year, getattr(rule, 'years', 'all'))
            if not _dims_match(f, rule.required_dims, cfg.axis_aliases):
                continue
            if not _unit_match(f, rule.units):
                continue
            if not _period_type_match(f, rule.period_type):
                continue
            if rule.filter_for_consolidated and not _is_consolidated(f, cfg.consolidated_members):
                continue

            # Memory optimization: For PICK_FIRST strategy, only collect first matching fact per alias priority
            key = (year, id(rule))
            if rule.strategy == MetricStrategy.PICK_FIRST:
                if key not in candidate_facts:  # First match for this rule
                    candidate_facts[key].append(f)
                else:
                    # Check if this fact's concept has higher priority than existing
                    existing_concept = candidate_facts[key][0].concept
                    existing_priority = rule.aliases.index(existing_concept) if existing_concept in rule.aliases else float('inf')
                    new_priority = rule.aliases.index(f.concept) if f.concept in rule.aliases else float('inf')
                    
                    if new_priority < existing_priority:
                        # This fact has higher priority, replace existing
                        candidate_facts[key] = [f]
                        logger.debug("  REPLACED metric candidate '%s' with higher priority alias: %s", rule.name, f.concept)
                    else:
                        logger.debug("  SKIPPED metric candidate '%s' - already have higher/equal priority match", rule.name)
            else:
                # For other strategies, collect all candidates
                candidate_facts[key].append(f)

    # Unified processing for both metrics and segments
    logger.debug("Processing %d candidate fact groups for final value extraction", len(candidate_facts))
    for (year, rule_id), candidates in candidate_facts.items():
        rule = rule_by_id[rule_id]
        rule_type = "segment" if isinstance(rule, SegmentRule) else "metric"
        logger.debug("Processing %s '%s' for year %s with %d candidates", rule_type, rule.name, year, len(candidates))
        
        # Get aliases based on rule type
        aliases = [rule.concept] if isinstance(rule, SegmentRule) else rule.aliases
        
        final_value: float | None = None
        
        if rule.strategy == MetricStrategy.PICK_FIRST:
            # Priority-based selection: find fact with highest-priority alias
            found_fact = None
            for alias in aliases:
                for fact in candidates:
                    if fact.concept == alias:
                        found_fact = fact
                        break
                if found_fact:
                    break
            if found_fact:
                final_value = found_fact.value
                logger.debug("  PICK_FIRST selected value: %s from concept %s", final_value, found_fact.concept)
        else:
            # Use accumulator for SUM, AVG, MAX, MIN, LATEST_IN_YEAR strategies
            acc = Accumulator()
            for fact in candidates:
                date = fact.period_key[1] or fact.period_key[0] or ""
                acc.update(fact.value, date)
                logger.debug("  Adding to accumulator: value=%s, date=%s", fact.value, date)
            final_value = acc.result(rule.strategy)
            logger.debug("  Strategy %s final result: %s", rule.strategy, final_value)
        
        # Place the final value in results
        if final_value is not None:
            if isinstance(rule, SegmentRule):
                ydict = results.setdefault(year, {})
                segdict = ydict.setdefault("segments", {})
                segdict[rule.name] = final_value
                logger.debug("  FINAL segment '%s' for year %s: %s", rule.name, year, final_value)
            else:  # metric
                _place_value(results, year, rule, rule.name, final_value)

    logger.debug("Extraction complete: processed %d facts, matched %d facts, extracted data for %d years", 
                facts_processed, facts_matched, len(results))
    logger.debug("Results by year: %s", {year: len(data) for year, data in results.items()})
    return results
