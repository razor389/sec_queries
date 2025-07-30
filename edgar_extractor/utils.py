import json
from pathlib import Path
from typing import Dict, Any, List, Union

from .config_schema import (
    CompanyConfig, SegmentRule, MetricRule, MetricStrategy,
    NewCompanyConfig, SegmentationRule, MetricConfig
)


def _json_load_strict(path: Path):
    txt = path.read_text(encoding="utf-8").strip()
    if not txt:
        raise ValueError(f"Empty config file: {path}")
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        raise ValueError(f"Bad JSON in {path}: {e}") from e


def _merge(a: dict, b: dict) -> dict:
    """Recursive, shallow-on-leaves merge of two dicts."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            out[k] = _merge(a.get(k, {}), v)
        else:
            out[k] = v
    return out


def _convert_new_to_legacy_format(new_config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert new config format to legacy format for backward compatibility."""
    legacy_config = {}
    
    # Handle profit_desc_metrics -> metrics + concept_aliases
    profit_metrics = new_config.get("profit_desc_metrics", {})
    legacy_config["concept_aliases"] = {}
    legacy_config["metrics"] = []
    
    for name, config in profit_metrics.items():
        if isinstance(config, list):
            # Handle list of year-based configurations
            for item in config:
                if isinstance(item, dict):
                    # Year-based configuration
                    aliases = item.get("aliases", [])
                    legacy_config["metrics"].append({
                        "name": name,
                        "aliases": aliases,
                        "strategy": item.get("strategy", "pick_first"),
                        "required_dims": item.get("required_dims"),
                        "units": item.get("units"),
                        "period_type": item.get("period_type"),
                        "filter_for_consolidated": item.get("filter_for_consolidated", False),
                        "years": item.get("years")
                    })
                else:
                    # Simple list format (backward compatibility)
                    if name not in legacy_config["concept_aliases"]:
                        legacy_config["concept_aliases"][name] = []
                    legacy_config["concept_aliases"][name].append(item)
        elif isinstance(config, str):
            # Single string format
            legacy_config["concept_aliases"][name] = [config]
        elif isinstance(config, dict):
            # Complex format with years, strategy, etc.
            aliases = config.get("aliases", [])
            legacy_config["metrics"].append({
                "name": name,
                "aliases": aliases,
                "strategy": config.get("strategy", "pick_first"),
                "required_dims": config.get("required_dims"),
                "units": config.get("units"),
                "period_type": config.get("period_type"),
                "filter_for_consolidated": config.get("filter_for_consolidated", False),
                "years": config.get("years")
            })
    
    # Handle balance_sheet_metrics -> balance_sheet_concepts
    balance_metrics = new_config.get("balance_sheet_metrics", {})
    legacy_config["balance_sheet_concepts"] = {}
    
    for name, config in balance_metrics.items():
        if isinstance(config, list):
            # Simple list format
            legacy_config["balance_sheet_concepts"][name] = config
        elif isinstance(config, str):
            # Single string format  
            legacy_config["balance_sheet_concepts"][name] = [config]
        elif isinstance(config, dict):
            # Complex format with years
            legacy_config["balance_sheet_concepts"][name] = config
    
    # Handle segmentation
    segmentation = new_config.get("segmentation", [])
    if isinstance(segmentation, dict) and "config" in segmentation:
        # Extract segmentation config
        seg_config = segmentation["config"]
        legacy_config["consolidated_members"] = seg_config.get("consolidated_members", [])
        legacy_config["axis_aliases"] = seg_config.get("axis_aliases", {})
        legacy_config["segments"] = []
    elif isinstance(segmentation, list):
        # List of segment rules
        legacy_config["segments"] = []
        for seg in segmentation:
            legacy_config["segments"].append({
                "name": seg["name"],
                "concept": seg["tag"],
                "required_dims": seg.get("explicitMembers", {}),
                "years": seg.get("years"),
                "strategy": seg.get("strategy", "pick_first"),
                "units": seg.get("units"),
                "period_type": seg.get("period_type"),
                "filter_for_consolidated": seg.get("filter_for_consolidated", False)
            })
    
    # Copy other fields as-is
    for key in ["balance_sheet_categories"]:
        if key in new_config:
            legacy_config[key] = new_config[key]
    
    return legacy_config


def _detect_config_format(config_data: Dict[str, Any]) -> str:
    """Detect whether config is in new or legacy format."""
    # Check for new format indicators
    if "profit_desc_metrics" in config_data or "balance_sheet_metrics" in config_data:
        return "new"
    # Check for legacy format indicators
    elif "metrics" in config_data or "concept_aliases" in config_data:
        return "legacy"
    else:
        # Default to legacy for backward compatibility
        return "legacy"


def _parse_metrics(merged: Dict[str, Any]) -> List[MetricRule]:
    metrics_conf = merged.get("metrics", [])
    out: List[MetricRule] = []

    # Backwards compatibility for concept_aliases["revenues"] etc.
    concept_aliases = merged.get("concept_aliases", {})
    if concept_aliases:
        for name, aliases in concept_aliases.items():
            # Only include if not supplied in metrics already
            if not any(m.get("name") == name for m in metrics_conf):
                metrics_conf.append({
                    "name": name,
                    "aliases": aliases,
                    "strategy": "pick_first",
                })

    # Balance sheet concepts: support both legacy list format and new dict format with years
    bs_conf = merged.get("balance_sheet_concepts", {})
    for cat, config in bs_conf.items():
        # Legacy format: list of aliases
        if isinstance(config, list):
            for alias in config:
                metrics_conf.append({
                    "name": alias,
                    "aliases": [alias],
                    "strategy": "latest_in_year",
                    "category": f"balance_sheet.{cat}",
                })
        # New format: dict with aliases and optional years
        elif isinstance(config, dict):
            aliases = config.get("aliases", [])
            years = config.get("years")
            for alias in aliases:
                rule = {
                    "name": alias,
                    "aliases": [alias],
                    "strategy": "latest_in_year",
                    "category": f"balance_sheet.{cat}",
                }
                if years:
                    rule["years"] = years
                metrics_conf.append(rule)

    for m in metrics_conf:
        out.append(
            MetricRule(
                name=m["name"],
                aliases=m["aliases"],
                strategy=MetricStrategy(m.get("strategy", "pick_first")),
                required_dims=m.get("required_dims"),
                units=m.get("units"),
                period_type=m.get("period_type"),
                category=m.get("category"),
                filter_for_consolidated=m.get("filter_for_consolidated", False),
                years=m.get("years"),
            )
        )
    return out


def load_company_config(path: str, ticker: str) -> CompanyConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    data = _json_load_strict(p)

    default_cfg = data.get("default", {})
    companies = data.get("companies", {})
    comp_cfg = companies.get(ticker, {})

    # Detect config format and convert if needed
    default_format = _detect_config_format(default_cfg)
    comp_format = _detect_config_format(comp_cfg)
    
    # Convert new format to legacy format for backward compatibility
    if default_format == "new":
        default_cfg = _convert_new_to_legacy_format(default_cfg)
    if comp_format == "new":
        comp_cfg = _convert_new_to_legacy_format(comp_cfg)

    merged = _merge(default_cfg, comp_cfg)

    # segments
    segs = [SegmentRule(**s) for s in merged.get("segments", [])]

    # metrics (new)
    metrics = _parse_metrics(merged)

    return CompanyConfig(
        axis_aliases=merged.get("axis_aliases", {}),
        consolidated_members=merged.get("consolidated_members", []),
        metrics=metrics,
        segments=segs,
    )
