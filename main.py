import argparse
import logging
import os
from pathlib import Path

from edgar_extractor import SECClient, load_company_config, XBRLIndex, extract_all

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s')
logger = logging.getLogger(__name__)

USER_AGENT = f"{os.getenv('USER_NAME', 'Unknown User')} ({os.getenv('USER_EMAIL', 'unknown@example.com')})"


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


def cli():
    parser = argparse.ArgumentParser(description="EDGAR XBRL extractor")
    parser.add_argument("ticker", help="Ticker symbol, e.g., PGR")
    parser.add_argument("--config", default="config/sample_config.json")
    parser.add_argument("--form", default="10-K")
    parser.add_argument("--accession", help="Specific accession number to debug")
    parser.add_argument("--dump-facts", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = cli()
    debug_one_filing(args.ticker, args.accession, args.form, args.config)
