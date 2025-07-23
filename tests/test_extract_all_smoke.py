# tests/test_extract_all_smoke.py
import textwrap

from edgar_extractor.xbrl_index import XBRLIndex
from edgar_extractor.metrics import extract_all
from edgar_extractor.config_schema import (
    CompanyConfig,
    MetricRule,
    MetricStrategy,
    SegmentRule,
)

# Minimal XBRL instance: one duration fact (revenues) and one instant fact (assets)
SIMPLE_XML = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:us-gaap="http://fasb.org/us-gaap/2024-01-31"
            xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
  <xbrli:context id="D2024">
    <xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">0000000000</xbrli:identifier>
    </xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2024-01-01</xbrli:startDate>
      <xbrli:endDate>2024-12-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>

  <xbrli:context id="I2024">
    <xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">0000000000</xbrli:identifier>
    </xbrli:entity>
    <xbrli:period>
      <xbrli:instant>2024-12-31</xbrli:instant>
    </xbrli:period>
  </xbrli:context>

  <xbrli:unit id="USD">
    <xbrli:measure>iso4217:USD</xbrli:measure>
  </xbrli:unit>

  <us-gaap:Revenues contextRef="D2024" unitRef="USD" decimals="0">12345</us-gaap:Revenues>
  <us-gaap:Assets contextRef="I2024" unitRef="USD" decimals="0">67890</us-gaap:Assets>
</xbrli:xbrl>
""")


def test_extract_all_smoke():
    # Build minimal config:
    cfg = CompanyConfig(
        metrics=[
            MetricRule(
                name="revenues",
                aliases=["us-gaap:Revenues"],
                strategy=MetricStrategy.PICK_FIRST,
            ),
            MetricRule(
                name="us-gaap:Assets",
                aliases=["us-gaap:Assets"],
                strategy=MetricStrategy.LATEST_IN_YEAR,
                category="balance_sheet.assets",
            ),
        ],
        segments=[
            # No segments in this smoke test, but keep list for completeness
        ],
        consolidated_members=[],
    )

    index = XBRLIndex(SIMPLE_XML)
    results = extract_all(index, cfg)

    # Basic shape checks
    assert "2024" in results
    assert results["2024"]["revenues"] == 12345
    assert "balance_sheet" in results["2024"]
    assert "assets" in results["2024"]["balance_sheet"]
    assert results["2024"]["balance_sheet"]["assets"]["us-gaap:Assets"] == 67890
