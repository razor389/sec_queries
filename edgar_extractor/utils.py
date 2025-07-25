import json
from pathlib import Path
from typing import Dict, Any, List

from .config_schema import CompanyConfig, SegmentRule, MetricRule, MetricStrategy


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


def _parse_metrics(merged: Dict[str, Any]) -> List[MetricRule]:
    metrics_conf = merged.get("metrics", [])
    out: List[MetricRule] = []

    # Backwards compatibility for concept_aliases["revenues"] etc.
    concept_aliases = merged.get("concept_aliases", {})
    if concept_aliases:
        for name, aliases in concept_aliases.items():
            # Only include if not supplied in metrics already
            if not any(m.name == name for m in metrics_conf):
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

    merged = _merge(default_cfg, comp_cfg)

    # segments
    segs = [SegmentRule(**s) for s in merged.get("segments", [])]

    # metrics (new)
    metrics = _parse_metrics(merged)

    return CompanyConfig(
        concept_aliases=merged.get("concept_aliases", {}),
        axis_aliases=merged.get("axis_aliases", {}),
        consolidated_members=merged.get("consolidated_members", []),
        metrics=metrics,
        segments=segs,
        balance_sheet_concepts=merged.get("balance_sheet_concepts", {}),
    )
