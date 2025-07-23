import argparse
import logging
import os
from pathlib import Path

from edgar_extractor import SECClient, load_company_config, XBRLIndex, extract_all

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s')
logger = logging.getLogger(__name__)

USER_AGENT = f"{os.getenv('USER_NAME', 'Unknown User')} ({os.getenv('USER_EMAIL', 'unknown@example.com')})"


def extract_multi_year_data(ticker: str, form: str, config_path: str, target_years: int = 7) -> tuple[dict, dict]:
    """
    Extract data for multiple years by fetching filings that contain comparative data.
    Each filing typically contains 3-4 years of data.
    
    Returns:
        tuple: (results_dict, year_to_filing_dict)
    """
    logger.info("Starting multi-year extraction for ticker=%s, target_years=%d", ticker, target_years)
    cfg = load_company_config(config_path, ticker)
    client = SECClient(USER_AGENT)
    cik = client.get_cik_from_ticker(ticker)
    
    combined_results = {}
    year_to_filing = {}  # Track which filing each year came from
    current_year = 2024
    years_needed = set(range(current_year - target_years + 1, current_year + 1))
    logger.info("Target years: %s", sorted(years_needed))
    
    # Get available filings
    filings = client.list_filings(cik, form=form, count=10)
    logger.info("Found %d available filings", len(filings))
    
    for i, filing in enumerate(filings):
        if not years_needed:  # We have all the years we need
            break
            
        logger.info("Processing filing %d: %s (date: %s)", i+1, filing['accession'], filing.get('date', 'unknown'))
        
        try:
            # Extract data from this filing
            xml_url = client.get_instance_xml_url(filing["filing_url"])
            xml_text = client.fetch_xml(xml_url)
            index = XBRLIndex(xml_text)
            results = extract_all(index, cfg)
            
            years_added = []
            # Add new years to combined results (only if we need them and don't have them)
            for year, data in results.items():
                year_int = int(year)
                if year not in combined_results and year_int in years_needed:
                    combined_results[year] = data
                    year_to_filing[year] = {
                        'accession': filing['accession'],
                        'filing_url': filing['filing_url'],
                        'date': filing.get('date', 'unknown')
                    }
                    years_needed.discard(year_int)
                    years_added.append(year)
            
            if years_added:
                logger.info("Added years %s from filing %s", sorted(years_added), filing['accession'])
            else:
                logger.info("No new years added from filing %s", filing['accession'])
            
            logger.info("Years still needed: %s", sorted(years_needed) if years_needed else "None")
            
        except Exception as e:
            logger.warning("Failed to process filing %s: %s", filing['accession'], e)
            continue
    
    missing_years = sorted(years_needed) if years_needed else []
    if missing_years:
        logger.warning("Could not find data for years: %s", missing_years)
    
    logger.info("Multi-year extraction complete: extracted %d years", len(combined_results))
    return combined_results, year_to_filing


def debug_one_filing(ticker: str, accession: str | None, form: str, config_path: str):
    logger.info("Starting debug_one_filing for ticker=%s, form=%s, config=%s", ticker, form, config_path)
    logger.info("Loading company config from %s for ticker %s", config_path, ticker)
    cfg = load_company_config(config_path, ticker)
    logger.debug("Config loaded: %d metric rules, %d segment rules", len(cfg.metrics), len(cfg.segments))
    logger.info("Creating SECClient with user agent: %s", USER_AGENT)
    client = SECClient(USER_AGENT)
    logger.info("Getting CIK for ticker %s", ticker)
    cik = client.get_cik_from_ticker(ticker)
    logger.info("CIK found: %s", cik)

    logger.info("Listing filings for CIK %s, form %s, count 10", cik, form)
    filings = client.list_filings(cik, form=form, count=10)
    logger.info("Found %d filings", len(filings))
    logger.debug("Filings: %s", [f['accession'] for f in filings[:3]])
    if accession:
        logger.info("Looking for specific accession: %s", accession)
        chosen = next((f for f in filings if f["accession"] == accession), None)
        if not chosen:
            logger.error("Accession %s not found among %d fetched filings", accession, len(filings))
            raise SystemExit(f"Accession {accession} not found among fetched filings")
        logger.info("Found requested accession: %s", accession)
    else:
        chosen = filings[0]
        logger.info("Using most recent filing: %s (date: %s)", chosen['accession'], chosen.get('date', 'unknown'))

    logger.info("Getting XML instance URL from filing URL: %s", chosen["filing_url"])
    xml_url = client.get_instance_xml_url(chosen["filing_url"])
    logger.info("XML instance URL found: %s", xml_url)
    logger.info("Fetching XML content from: %s", xml_url)
    xml_text = client.fetch_xml(xml_url)
    logger.info("XML fetched successfully, length: %d chars", len(xml_text))

    logger.info("Parsing XBRL from XML text")
    index = XBRLIndex(xml_text)
    logger.info("XBRL index created: %d facts, %d contexts", len(index.facts), len(index.contexts))
    logger.debug("Available years: %s", sorted(index.list_years()))
    logger.info("Extracting metrics from XBRL index")
    results = extract_all(index, cfg)
    logger.info("Metrics extracted for %d years: %s", len(results), sorted(results.keys()))

    logger.info("Extraction complete, displaying results")
    print("=== RESULTS ===")
    for year, data in sorted(results.items()):
        print(year, data)
    
    if args.dump_facts:
        print("\n--- ALL FACT KEYS (concept, period, dims) ---")
        for k, f in index.facts.items():
            print(k, f.value)
    
    # Debug: Show all available years and what data exists
    print("\n=== YEAR COVERAGE ANALYSIS ===")
    all_years = sorted(index.list_years())
    print(f"All years in XBRL: {all_years}")
    extracted_years = sorted(results.keys())
    print(f"Years with extracted data: {extracted_years}")
    print(f"📄 Filing URL: {chosen['filing_url']}")
    
    # Show what's available for each year
    for year in all_years:
        year_facts = [f for f in index.facts.values() if year in str(f.period_key)]
        concepts = set(f.concept for f in year_facts)
        print(f"{year}: {len(year_facts)} facts, concepts include: {sorted(list(concepts))[:5]}...")

    if args.dump_facts:
        print("\n--- ALL FACT KEYS (concept, period, dims) ---")
        for k, f in index.facts.items():
            print(k, f.value)


def cli():
    parser = argparse.ArgumentParser(description="EDGAR XBRL extractor")
    parser.add_argument("ticker", help="Ticker symbol, e.g., PGR")
    parser.add_argument("--config", default="config/sample_config.json")
    parser.add_argument("--form", default="10-K")
    parser.add_argument("--accession", help="Specific accession number to debug (single filing mode)")
    parser.add_argument("--single-filing", action="store_true", help="Extract from single filing only")
    parser.add_argument("--years", type=int, default=7, help="Target number of years to extract")
    parser.add_argument("--dump-facts", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    
    if args.accession or args.single_filing:
        # Single filing mode (original functionality)
        logger.info("Running in single-filing mode")
        debug_one_filing(args.ticker, args.accession, args.form, args.config)
    else:
        # Multi-year mode (new default)
        logger.info("Running in multi-year mode")
        results, year_to_filing = extract_multi_year_data(args.ticker, args.form, args.config, args.years)
        
        print("=== MULTI-YEAR RESULTS ===")
        for year, data in sorted(results.items()):
            filing_info = year_to_filing.get(year, {})
            filing_url = filing_info.get('filing_url', 'Unknown')
            print(f"{year}: {data}")
            print(f"📄 Filing URL: {filing_url}")
        
        print(f"\nExtracted data for {len(results)} years: {sorted(results.keys())}")
