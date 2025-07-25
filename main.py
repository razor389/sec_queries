import argparse
import logging
import os
from pathlib import Path

from edgar_extractor import SECClient, load_company_config, XBRLIndex, extract_all
from edgar_extractor.metrics import _report_missing_data

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s')
logger = logging.getLogger(__name__)

USER_AGENT = f"{os.getenv('USER_NAME', 'Unknown User')} ({os.getenv('USER_EMAIL', 'unknown@example.com')})"


def _is_year_data_complete(year: str, data: dict, cfg) -> bool:
    """
    Check if extracted year data is complete based on configuration expectations.
    A year is complete if it has segments when segments are configured for that year.
    """
    year_int = int(year)
    
    # Check if any segment rules apply to this year
    has_expected_segments = False
    for segment_rule in cfg.segments:
        # Import the year_matches_range function
        from edgar_extractor.config_schema import year_matches_range
        if year_matches_range(year_int, getattr(segment_rule, 'years', None)):
            has_expected_segments = True
            break
    
    # If segments are expected for this year, check if we have them
    if has_expected_segments:
        segments = data.get('segments', {})
        if not segments:
            logger.debug("Year %s is incomplete - expected segments but found none", year)
            return False
        
        # Could add more detailed checking here if needed
        logger.debug("Year %s appears complete - has %d segments", year, len(segments))
    
    return True


def _count_data_items(data: dict) -> int:
    """Count the total number of data items in a year's extracted data"""
    count = 0
    for key, value in data.items():
        if key == "segments" and isinstance(value, dict):
            count += len(value)
        elif key == "balance_sheet" and isinstance(value, dict):
            for bs_category in value.values():
                if isinstance(bs_category, dict):
                    count += len(bs_category)
        else:
            count += 1
    return count


def _find_missing_data(year_data: dict, cfg, year: int) -> dict:
    """Find what data is missing for a specific year based on configuration"""
    missing = {"metrics": [], "segments": [], "balance_sheet": {}}
    
    # Check for missing segments
    segments_data = year_data.get("segments", {})
    for segment_rule in cfg.segments:
        from edgar_extractor.config_schema import year_matches_range
        if year_matches_range(year, segment_rule.years):
            if segment_rule.name not in segments_data:
                missing["segments"].append(segment_rule.name)
    
    # Check for missing metrics (basic check for key metrics)
    if "revenues" not in year_data:
        missing["metrics"].append("revenues")
    
    # Check for missing balance sheet categories
    balance_sheet = year_data.get("balance_sheet", {})
    if not balance_sheet:
        missing["balance_sheet"]["any"] = True
    else:
        # Check if assets are missing
        if "assets" not in balance_sheet:
            missing["balance_sheet"]["assets"] = True
    
    return missing


def _merge_missing_data(primary_data: dict, additional_data: dict, missing: dict, year: int) -> tuple[dict, list]:
    """Merge missing data from additional_data into primary_data"""
    merged_data = primary_data.copy()
    filled_items = []
    
    # Fill missing segments
    if missing["segments"]:
        additional_segments = additional_data.get("segments", {})
        primary_segments = merged_data.setdefault("segments", {})
        
        for missing_segment in missing["segments"]:
            if missing_segment in additional_segments:
                primary_segments[missing_segment] = additional_segments[missing_segment]
                filled_items.append(f"segment:{missing_segment}")
    
    # Fill missing metrics
    for missing_metric in missing["metrics"]:
        if missing_metric in additional_data:
            merged_data[missing_metric] = additional_data[missing_metric]
            filled_items.append(f"metric:{missing_metric}")
    
    # Fill missing balance sheet data
    if missing["balance_sheet"]:
        additional_bs = additional_data.get("balance_sheet", {})
        if additional_bs:
            primary_bs = merged_data.setdefault("balance_sheet", {})
            
            # For now, just fill missing assets
            if "assets" in missing["balance_sheet"] and "assets" in additional_bs:
                primary_bs["assets"] = additional_bs["assets"]
                filled_items.append("balance_sheet:assets")
    
    return merged_data, filled_items


def _get_actual_filing_url_for_year(client: 'SECClient', cik: str, year: int, form: str) -> str:
    """
    Get the original filing URL for a specific data year.
    For 10-K filings, year N data is typically filed in year N+1.
    """
    try:
        # Get more filings to ensure we find the right year
        filings = client.list_filings(cik, form=form, count=30)
        
        # For 10-K filings, year N data is typically filed in year N+1
        # So look for filings in year+1 first, then year, then year+2
        search_years = [year + 1, year, year + 2]
        
        for search_year in search_years:
            search_year_str = str(search_year)
            for filing in filings:
                filing_date = filing.get('date', '')
                if filing_date.startswith(search_year_str):
                    logger.debug("Found filing for year %d data: %s (filed %s)", year, filing['accession'], filing_date)
                    return filing['filing_url']
        
        # If no match found, return first filing as fallback
        if filings:
            logger.warning("Could not find specific filing for year %d data, using fallback", year)
            return filings[0]['filing_url']
        
        return "Unknown"
    except Exception as e:
        logger.warning("Failed to get filing URL for year %d: %s", year, e)
        return "Unknown"


def extract_multi_year_data(ticker: str, form: str, config_path: str, target_years: int = 7) -> tuple[dict, dict]:
    """
    Extract data for multiple years using year-centric approach.
    For each target year, we find the most authoritative filing (typically filed in year+1)
    and use that as the primary source, allowing later filings to update/correct if needed.
    
    Returns:
        tuple: (results_dict, year_to_filing_dict)
    """
    logger.info("Starting year-centric multi-year extraction for ticker=%s, target_years=%d", ticker, target_years)
    cfg = load_company_config(config_path, ticker)
    client = SECClient(USER_AGENT)
    cik = client.get_cik_from_ticker(ticker)
    
    combined_results = {}
    year_to_filing = {}  # Track which filing each year came from
    current_year = 2024
    target_year_list = list(range(current_year - target_years + 1, current_year + 1))
    logger.info("Target years: %s", target_year_list)
    
    # Get available filings
    filings = client.list_filings(cik, form=form, count=15)  # Get more filings to ensure coverage
    logger.info("Found %d available filings", len(filings))
    
    # Create a year-to-authoritative-filing mapping
    # For 10-K filings, year N data is typically filed in year N+1
    year_to_primary_filing = {}
    filing_to_info = {}
    
    for filing in filings:
        filing_date = filing.get('date', '')
        if filing_date:
            filing_year = int(filing_date[:4])
            # This filing likely contains data for year (filing_year - 1)
            data_year = filing_year - 1
            if data_year in target_year_list and data_year not in year_to_primary_filing:
                year_to_primary_filing[data_year] = filing
                filing_to_info[filing['accession']] = filing
                logger.info("Mapped year %d to primary filing %s (filed %s)", data_year, filing['accession'], filing_date)
    
    # Process years in chronological order (oldest first) using their primary filings
    for year in sorted(target_year_list):
        if year not in year_to_primary_filing:
            logger.warning("No primary filing found for year %d", year)
            continue
            
        filing = year_to_primary_filing[year]
        logger.info("Processing year %d from primary filing %s (date: %s)", year, filing['accession'], filing.get('date', 'unknown'))
        
        try:
            # Extract data from this filing
            xml_url = client.get_instance_xml_url(filing["filing_url"])
            xml_text = client.fetch_xml(xml_url)
            index = XBRLIndex(xml_text)
            results = extract_all(index, cfg)
            
            # For the primary filing of this year, always take the data
            year_str = str(year)
            if year_str in results:
                combined_results[year_str] = results[year_str]
                year_to_filing[year_str] = {
                    'accession': filing['accession'],
                    'filing_url': filing['filing_url'],
                    'date': filing.get('date', 'unknown')
                }
                logger.info("Extracted year %d data from primary filing %s", year, filing['accession'])
            else:
                logger.warning("Primary filing %s does not contain data for year %d", filing['accession'], year)
            
        except Exception as e:
            logger.warning("Failed to process primary filing %s for year %d: %s", filing['accession'], year, e)
            continue
    
    # Phase 2: Identify missing data and backfill from subsequent filings
    logger.info("Phase 2: Identifying missing data and backfilling from subsequent filings")
    
    # First, identify what's missing for each year
    years_with_missing_data = {}
    for year_str, year_data in combined_results.items():
        year = int(year_str)
        missing = _find_missing_data(year_data, cfg, year)
        if missing["segments"] or missing["metrics"] or missing["balance_sheet"]:
            years_with_missing_data[year] = missing
            logger.info("Year %d missing: segments=%s, metrics=%s, balance_sheet=%s", 
                       year, missing["segments"], missing["metrics"], list(missing["balance_sheet"].keys()))
    
    # If we have missing data, look through ALL filings to fill gaps
    if years_with_missing_data:
        # Check all filings, including ones we used as primary filings for other years
        # Later filings often contain historical segment data that wasn't in the original filing
        backfill_filings = filings  # Check ALL filings
        logger.info("Checking %d filings for missing data backfill", len(backfill_filings))
        
        for filing in backfill_filings:
            if not years_with_missing_data:  # All gaps filled
                break
                
            logger.info("Checking filing %s for missing data (date: %s)", filing['accession'], filing.get('date', 'unknown'))
            
            try:
                xml_url = client.get_instance_xml_url(filing["filing_url"])
                xml_text = client.fetch_xml(xml_url)
                index = XBRLIndex(xml_text)
                results = extract_all(index, cfg)
                
                # Check if this filing can fill missing data for any year
                for year, missing in list(years_with_missing_data.items()):
                    year_str = str(year)
                    if year_str in results:
                        # Try to fill missing data
                        merged_data, filled_items = _merge_missing_data(
                            combined_results[year_str], 
                            results[year_str], 
                            missing, 
                            year
                        )
                        
                        if filled_items:
                            logger.info("Filled missing data for year %d from filing %s: %s", 
                                       year, filing['accession'], ', '.join(filled_items))
                            combined_results[year_str] = merged_data
                            
                            # Update the missing data tracker
                            updated_missing = _find_missing_data(merged_data, cfg, year)
                            if not (updated_missing["segments"] or updated_missing["metrics"] or updated_missing["balance_sheet"]):
                                # All missing data filled for this year
                                del years_with_missing_data[year]
                                logger.info("Year %d now complete", year)
                            else:
                                years_with_missing_data[year] = updated_missing
                
            except Exception as e:
                logger.warning("Failed to process backfill filing %s: %s", filing['accession'], e)
                continue
        
        # Report any remaining missing data
        if years_with_missing_data:
            logger.warning("Could not fill all missing data:")
            for year, missing in years_with_missing_data.items():
                logger.warning("Year %d still missing: segments=%s, metrics=%s, balance_sheet=%s", 
                              year, missing["segments"], missing["metrics"], list(missing["balance_sheet"].keys()))
    
    # Update year_to_filing to use actual filing URLs for each year
    logger.info("Looking up actual filing URLs for each extracted year...")
    for year in combined_results.keys():
        year_int = int(year)
        actual_filing_url = _get_actual_filing_url_for_year(client, cik, year_int, form)
        if year in year_to_filing:
            year_to_filing[year]['actual_filing_url'] = actual_filing_url
        else:
            year_to_filing[year] = {
                'accession': 'Unknown',
                'filing_url': actual_filing_url,
                'actual_filing_url': actual_filing_url,
                'date': f'{year}-12-31'
            }
    
    # Check which target years we successfully extracted
    extracted_year_ints = [int(year) for year in combined_results.keys()]
    missing_years = [year for year in target_year_list if year not in extracted_year_ints]
    if missing_years:
        logger.warning("Could not find data for years: %s", missing_years)
    
    # Report missing data for extracted years
    extracted_years = [int(year) for year in combined_results.keys()]
    if extracted_years:
        missing_data = _report_missing_data(combined_results, cfg, extracted_years)
        
        # Print missing data report
        if any(missing_data.values()):
            print("\n⚠️  MISSING DATA REPORT:")
            for category_type, categories in missing_data.items():
                if categories:
                    print(f"\n{category_type.upper()}:")
                    for category_name, missing_years_list in categories.items():
                        # Find the rule to show its aliases
                        rule = None
                        if category_type == "metrics":
                            rule = next((r for r in cfg.metrics if r.name == category_name), None)
                        elif category_type == "segments":
                            rule = next((r for r in cfg.segments if r.name == category_name), None)
                        
                        if rule and hasattr(rule, 'aliases'):
                            print(f"  • {category_name}: missing in years {missing_years_list} (searched for: {rule.aliases})")
                        else:
                            print(f"  • {category_name}: missing in years {missing_years_list}")
        else:
            print("\n✅ All configured categories have data for all extracted years")
    
    logger.info("Multi-year extraction complete: extracted %d years", len(combined_results))
    return combined_results, year_to_filing


def debug_one_filing(ticker: str, accession: str | None, form: str, config_path: str, dry_run: bool = False, dump_facts: bool = False):
    # Enable debug logging and show dry-run notification if in dry-run mode  
    if dry_run:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("--- RUNNING IN DRY-RUN MODE ---")
    
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
    
    if dump_facts:
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

    if dump_facts:
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
    parser.add_argument("--dry-run", action="store_true", help="Simulate extraction and show detailed matching logs without saving.")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    
    if args.accession or args.single_filing:
        # Single filing mode (original functionality)
        logger.info("Running in single-filing mode")
        debug_one_filing(args.ticker, args.accession, args.form, args.config, args.dry_run, args.dump_facts)
    else:
        # Multi-year mode (new default)
        logger.info("Running in multi-year mode")
        results, year_to_filing = extract_multi_year_data(args.ticker, args.form, args.config, args.years)
        
        print("=== MULTI-YEAR RESULTS ===")
        for year, data in sorted(results.items()):
            filing_info = year_to_filing.get(year, {})
            # Use actual_filing_url if available, otherwise fallback to filing_url
            filing_url = filing_info.get('actual_filing_url', filing_info.get('filing_url', 'Unknown'))
            print(f"{year}: {data}")
            print(f"📄 Filing URL: {filing_url}")
        
        print(f"\nExtracted data for {len(results)} years: {sorted(results.keys())}")
