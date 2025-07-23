from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from typing import Dict, Tuple, Any, List, DefaultDict
from collections import defaultdict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


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
        logger.info("Initializing XBRL index from XML text (%d chars)", len(xml_text))
        self.soup = BeautifulSoup(xml_text, "lxml-xml")
        logger.debug("XML parsed with BeautifulSoup")
        
        self.contexts = self._index_contexts()
        logger.info("Indexed %d contexts", len(self.contexts))
        
        # raw dict for unique keys
        self.facts: Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact] = self._index_facts()
        logger.info("Indexed %d facts", len(self.facts))
        
        # convenience indexes
        self.by_concept: DefaultDict[str, List[Fact]] = defaultdict(list)
        for f in self.facts.values():
            self.by_concept[f.concept].append(f)
        logger.debug("Built concept index with %d unique concepts", len(self.by_concept))

    # ---------------- internal helpers ----------------
    def _index_contexts(self) -> Dict[str, Dict[str, Any]]:
        logger.debug("Indexing contexts from XML")
        out = {}
        contexts_found = self.soup.find_all("context")
        logger.debug("Found %d context elements", len(contexts_found))
        for ctx in contexts_found:
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
        logger.debug("Context indexing complete: %d contexts processed", len(out))
        return out

    def _index_facts(self) -> Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact]:
        logger.debug("Indexing facts from XML")
        facts: Dict[Tuple[str, Tuple[str, str], Tuple[Tuple[str, str], ...]], Fact] = {}
        all_elements = self.soup.find_all(True)
        logger.debug("Found %d total XML elements to process", len(all_elements))
        
        skipped_no_colon = 0
        skipped_context = 0
        skipped_no_context_ref = 0
        skipped_no_text = 0
        skipped_not_numeric = 0
        skipped_no_context_data = 0
        
        fact_elements_seen = 0
        for elem in all_elements:
            # Debug actual fact elements (ones with prefixes)
            if elem.prefix and fact_elements_seen < 5:
                logger.debug("FACT Element: name='%s', prefix='%s', concept='%s:%s', attrs=%s, text='%s'", 
                           elem.name, elem.prefix, elem.prefix, elem.name, dict(elem.attrs), elem.get_text()[:50])
                fact_elements_seen += 1
            
            # Skip elements that are structural (context, units, etc.) rather than facts
            if (elem.name.lower() in ('xbrl', 'schemaref', 'context', 'entity', 'identifier', 
                                     'period', 'startdate', 'enddate', 'instant', 'segment', 
                                     'explicitmember', 'unit', 'measure') or
                elem.prefix in ('link', 'xbrldi')):
                skipped_context += 1
                continue
                
            # For XBRL facts, we need namespace prefix - use elem.prefix if available
            if elem.prefix:
                concept_name = f"{elem.prefix}:{elem.name}"
            else:
                # Skip elements without namespace prefix as they're likely structural
                skipped_no_colon += 1
                continue
            # This check is now handled above
            # if elem.name.lower().endswith("context"):
            #     skipped_context += 1
            #     continue
            ctxid = elem.get("contextRef") or elem.get("contextref")
            if ctxid is None:
                skipped_no_context_ref += 1
                continue
            text = (elem.text or "").strip()
            if not text:
                skipped_no_text += 1
                continue
            try:
                val = float(text.replace(",", ""))
            except ValueError:
                skipped_not_numeric += 1
                continue
            ctx = self.contexts.get(ctxid)
            if not ctx:
                skipped_no_context_data += 1
                continue
            unit = elem.get("unitRef") or elem.get("unitref") or ""
            decimals = elem.get("decimals")
            scale = elem.get("scale")
            if scale and scale != "0":
                val *= 10 ** int(scale)
            key = (concept_name, ctx["period_key"], tuple(sorted(ctx["dims"].items())))
            facts[key] = Fact(
                concept=concept_name,
                value=val,
                unit=unit,
                decimals=decimals,
                period_key=ctx["period_key"],
                dims=ctx["dims"],
                context_id=ctxid,
            )
        
        logger.info("Fact indexing complete: %d facts processed", len(facts))
        logger.debug("Skipped elements - no colon: %d, context: %d, no contextRef: %d, no text: %d, not numeric: %d, no context data: %d", 
                    skipped_no_colon, skipped_context, skipped_no_context_ref, skipped_no_text, skipped_not_numeric, skipped_no_context_data)
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