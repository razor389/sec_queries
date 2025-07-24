#!/usr/bin/env python3

import os
from edgar_extractor import SECClient, XBRLIndex

# Simple script to debug what segment revenue facts exist for 2021
def debug_2021_segments():
    USER_AGENT = f"{os.getenv('USER_NAME', 'Unknown User')} ({os.getenv('USER_EMAIL', 'unknown@example.com')})"
    client = SECClient(USER_AGENT)
    cik = client.get_cik_from_ticker("PRI")
    
    # Get the 2024 filing that contains 2021 data
    filings = client.list_filings(cik, form="10-K", count=10)
    filing_2024 = next(f for f in filings if f['accession'] == '0000950170-24-022262')
    
    print(f"Analyzing filing: {filing_2024['accession']}")
    print(f"Filing URL: {filing_2024['filing_url']}")
    
    # Get the XBRL data
    xml_url = client.get_instance_xml_url(filing_2024["filing_url"])
    xml_text = client.fetch_xml(xml_url)
    index = XBRLIndex(xml_text)
    
    print(f"\nTotal facts: {len(index.facts)}")
    
    # Find all revenue facts for 2021
    revenue_2021_facts = []
    for fact in index.facts.values():
        if fact.concept == "us-gaap:Revenues":
            # Extract year from fact
            start, end = fact.period_key
            date = end or start
            year = date[:4] if date else ""
            
            if year == "2021":
                revenue_2021_facts.append(fact)
    
    print(f"\nFound {len(revenue_2021_facts)} revenue facts for 2021:")
    
    for i, fact in enumerate(revenue_2021_facts):
        print(f"\n--- Revenue Fact {i+1} ---")
        print(f"Value: ${fact.value:,.0f}")
        print(f"Unit: {fact.unit}")
        print(f"Period: {fact.period_key}")
        print(f"Context ID: {fact.context_id}")
        print(f"Dimensions: {fact.dims}")
        
        # Check if this has segment dimensions
        has_segment_axis = 'us-gaap:StatementBusinessSegmentsAxis' in fact.dims
        if has_segment_axis:
            segment_member = fact.dims['us-gaap:StatementBusinessSegmentsAxis']
            print(f"✓ SEGMENT: {segment_member}")
        else:
            print("✗ No segment dimension")
    
    # Also check for any facts with segment dimensions in 2021
    print(f"\n\n=== ALL 2021 facts with segment dimensions ===")
    segment_facts_2021 = []
    for fact in index.facts.values():
        start, end = fact.period_key
        date = end or start
        year = date[:4] if date else ""
        
        if year == "2021" and 'us-gaap:StatementBusinessSegmentsAxis' in fact.dims:
            segment_facts_2021.append(fact)
    
    print(f"Found {len(segment_facts_2021)} facts with segment dimensions for 2021:")
    
    for fact in segment_facts_2021:
        segment_member = fact.dims['us-gaap:StatementBusinessSegmentsAxis']
        print(f"- {fact.concept}: ${fact.value:,.0f} [{segment_member}]")

if __name__ == "__main__":
    debug_2021_segments()