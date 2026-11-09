"""Command-line entrypoint for the Job Aggregation and ATS Resume Tailoring Pipeline."""
import argparse
import logging
import sys

from src.main import run_pipeline
from src.sheets.manager import SheetManager

logger = logging.getLogger("cli_entrypoint")


def main() -> None:
    """Parse CLI arguments and dispatch pipeline execution."""
    parser = argparse.ArgumentParser(
        description="Automated Zero-Cost Job Aggregator & ATS-Tailored Resume Generator",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of new jobs to process in this run (e.g., --limit 2).",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Only synchronize stranded rows from fallback_cache.csv to Google Sheets without scraping.",
    )

    args = parser.parse_args()

    if args.sync_only:
        logger.info("Executing in sync-only mode...")
        sm = SheetManager()
        synced = sm.sync_fallback_cache()
        logger.info("Sync complete. Recovered %d rows.", synced)
        sys.exit(0)

    run_pipeline(limit=args.limit)


if __name__ == "__main__":
    main()
