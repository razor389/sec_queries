import argparse
import logging
from pathlib import Path

from edgar_extractor import SECClient, load_company_config, XBRLIndex, extract_all

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

USER_AGENT = "Name (your.name@your.email.com)"


def debug_one_filing(ticker: str, accession: str | None, form: str, config_path: str):
    cfg = load_company_config(config_path, ticker)
    client = SECClient(USER_AGENT)
    cik = client.get_cik_from_ticker(ticker)

    filings = client.list_filings(cik, form=form, count=10)
    if accession:
        chosen = next((f for f in filings if f["accession"] == accession), None)
        if not chosen:
            raise SystemExit(f"Accession {accession} not found among fetched filings")
    else:
        chosen = filings[0]

    xml_url = client.get_instance_xml_url(chosen["filing_url"])
    xml_text = client.fetch_xml(xml_url)

    index = XBRLIndex(xml_text)
    results = extract_all(index, cfg)

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
