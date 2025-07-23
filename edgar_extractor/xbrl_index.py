from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Dict, Tuple, Any, List, DefaultDict
from collections import defaultdict
from bs4 import BeautifulSoup


@dataclass
class Fact:
    concept: str
    value: float
    unit: str
    decimals: str | None
    period_key: Tuple[str, str]  # (start, end) or ("", instant)
    dims: Dict[str, str]
    context_id: str


class XBRLIndex:
    def __init__(self, xml_text: str):
        self.soup = BeautifulSoup(xml_text, "lxml-xml")
        self.contexts = self._index_contexts()
        # raw dict for unique keys
        self.facts: Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact] = self._index_facts()
        # convenience indexes
        self.by_concept: DefaultDict[str, List[Fact]] = defaultdict(list)
        for f in self.facts.values():
            self.by_concept[f.concept].append(f)

    # ---------------- internal helpers ----------------
    def _index_contexts(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for ctx in self.soup.find_all("context"):
            cid = ctx.get("id")
            if not cid:
                continue
            period = ctx.find("period")
            start = end = instant = None
            if period:
                s = period.find("startDate")
                e = period.find("endDate")
                i = period.find("instant")
                start = s.text.strip() if s else None
                end = e.text.strip() if e else None
                instant = i.text.strip() if i else None
            # period_key unify
            if instant:
                pkey = ("", instant)
            else:
                pkey = (start or "", end or "")

            dims = {}
            seg = ctx.find("segment")
            if seg:
                for exp in seg.find_all(True):
                    name = exp.name.lower()
                    if "explicitmember" in name:
                        axis = exp.get("dimension")
                        member = exp.text.strip()
                        if axis and member:
                            dims[axis] = member
            out[cid] = {"period_key": pkey, "dims": dims}
        return out

    def _index_facts(self) -> Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact]:
        facts: Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact] = {}
        for elem in self.soup.find_all(True):
            if ":" not in elem.name:
                continue
            if elem.name.lower().endswith("context"):
                continue
            ctxid = elem.get("contextRef") or elem.get("contextref")
            if ctxid is None:
                continue
            text = (elem.text or "").strip()
            if not text:
                continue
            try:
                val = float(text.replace(",", ""))
            except ValueError:
                continue
            ctx = self.contexts.get(ctxid)
            if not ctx:
                continue
            unit = elem.get("unitRef") or elem.get("unitref") or ""
            decimals = elem.get("decimals")
            scale = elem.get("scale")
            if scale and scale != "0":
                val *= 10 ** int(scale)
            key = (elem.name, ctx["period_key"], tuple(sorted(ctx["dims"].items())))
            facts[key] = Fact(
                concept=elem.name,
                value=val,
                unit=unit,
                decimals=decimals,
                period_key=ctx["period_key"],
                dims=ctx["dims"],
                context_id=ctxid,
            )
        return facts

    # ---------------- public helpers ----------------
    @staticmethod
    def _year_from_period_key(pkey: Tuple[str, str]) -> str:
        start, end = pkey
        # prefer end (duration) or instant second slot
        date_str = end or start
        return date_str[:4] if date_str else ""

    def list_years(self) -> set[str]:
        years = set()
        for _, pkey, _ in self.facts.keys():
            y = self._year_from_period_key(pkey)
            if y:
                years.add(y)
        return years