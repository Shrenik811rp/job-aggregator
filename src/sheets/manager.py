"""Google Sheets persistence manager with local CSV fallback."""
import csv
import logging
from typing import List, Set

import gspread

from src.config import CREDENTIALS_FILE_PATH, FALLBACK_CACHE_PATH, GOOGLE_SHEET_NAME

logger = logging.getLogger(__name__)

HEADERS: List[str] = [
    "Timestamp",
    "Job Title",
    "Location",
    "Job Link",
    "Extracted Job Description",
    "Tailored Resume PDF",
    "Resume LaTeX Template",
    "Tailored Cover Letter PDF",
    "Cover Letter LaTeX Template",
    "Status",
]


class SheetManager:
    """Manages Google Sheets tracking with automated local CSV fallback."""

    def __init__(self) -> None:
        self.client = None
        self.spreadsheet = None
        self.sheet = None

        try:
            self.client = gspread.service_account(filename=str(CREDENTIALS_FILE_PATH))
            self.spreadsheet = self.client.open(GOOGLE_SHEET_NAME)
            self.sheet = self.spreadsheet.sheet1
            logger.info("Successfully connected to Google Sheet '%s'", GOOGLE_SHEET_NAME)
        except Exception as e:
            logger.warning("Failed to authenticate or connect to Google Sheets: %s", e)
            self.client = None
            self.spreadsheet = None
            self.sheet = None

    def get_processed_links(self) -> Set[str]:
        """Fetch all values from Column 4 ("Job Link") starting from row 2.

        Returns a Python set of URLs for fast O(1) deduplication.
        If the sheet cannot be reached or is empty, returns an empty set.
        """
        if not self.sheet:
            return set()

        try:
            col_values = self.sheet.col_values(4)
            if len(col_values) > 1:
                return {val.strip() for val in col_values[1:] if val and val.strip()}
            return set()
        except Exception as e:
            logger.warning("Failed to fetch processed links from Google Sheets: %s", e)
            return set()

    def append_job_row(self, row_data: list) -> bool:
        """Append a job row to Google Sheets or route to local fallback cache.

        Expects a list of 10 items matching the standard schema:
        [Timestamp, Job Title, Location, Job Link, Extracted Job Description,
         Tailored Resume PDF, Resume LaTeX Template, Tailored Cover Letter PDF,
         Cover Letter LaTeX Template, Status]

        Returns True if appended to Google Sheets, False if routed to fallback cache.
        """
        if not self.sheet:
            print("Google Sheet is not connected. Routing row to fallback cache.")
            logger.warning("Google Sheet is not connected. Routing row to fallback cache.")
            self._write_to_fallback_cache(row_data)
            return False

        try:
            self.sheet.append_row(row_data, value_input_option="USER_ENTERED")
            return True
        except Exception as e:
            print(f"Error appending row to Google Sheets: {e}")
            logger.error("Error appending row to Google Sheets: %s", e)
            self._write_to_fallback_cache(row_data)
            return False

    def _write_to_fallback_cache(self, row_data: list) -> None:
        """Append row_data to FALLBACK_CACHE_PATH as a standard CSV row.

        If the file does not exist or is empty, creates it with the standard 10 headers.
        """
        try:
            file_exists = FALLBACK_CACHE_PATH.exists() and FALLBACK_CACHE_PATH.stat().st_size > 0
            FALLBACK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

            with open(FALLBACK_CACHE_PATH, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(HEADERS)
                writer.writerow(row_data)
            logger.info("Wrote row to fallback cache: %s", FALLBACK_CACHE_PATH)
        except Exception as e:
            logger.error("Failed to write row to fallback cache: %s", e)

    def sync_fallback_cache(self) -> int:
        """Check if FALLBACK_CACHE_PATH exists and is non-empty.

        Reads cached rows, appends them sequentially to Google Sheets,
        and deletes or clears fallback_cache.csv upon successful upload.

        Returns the count of recovered rows synced to Google Sheets.
        """
        if not FALLBACK_CACHE_PATH.exists() or FALLBACK_CACHE_PATH.stat().st_size == 0:
            return 0

        if not self.sheet:
            logger.warning("Cannot sync fallback cache: Google Sheet is not connected.")
            return 0

        rows_to_sync: List[List[str]] = []
        try:
            with open(FALLBACK_CACHE_PATH, mode="r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                try:
                    header = next(reader)
                except StopIteration:
                    return 0

                # If first row is not the header row, treat it as data
                if header != HEADERS and not (header and header[0].strip() == "Timestamp"):
                    rows_to_sync.append(header)

                for row in reader:
                    if row:
                        rows_to_sync.append(row)
        except Exception as e:
            logger.error("Failed to read fallback cache for syncing: %s", e)
            return 0

        if not rows_to_sync:
            # File had only headers or was empty
            try:
                FALLBACK_CACHE_PATH.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Failed to clean empty fallback cache: %s", e)
            return 0

        synced_count = 0
        failed_index = None

        for i, row in enumerate(rows_to_sync):
            try:
                self.sheet.append_row(row, value_input_option="USER_ENTERED")
                synced_count += 1
            except Exception as e:
                logger.error("Failed to sync cached row %d to Google Sheets: %s", i, e)
                failed_index = i
                break

        if failed_index is not None:
            # Re-save remaining rows that were not synced
            remaining_rows = rows_to_sync[failed_index:]
            try:
                with open(FALLBACK_CACHE_PATH, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(HEADERS)
                    writer.writerows(remaining_rows)
                logger.info(
                    "Partially synced %d rows. %d rows remain in fallback cache.",
                    synced_count,
                    len(remaining_rows),
                )
            except Exception as e:
                logger.error("Failed to re-write remaining rows to fallback cache: %s", e)
        else:
            # All rows successfully synced; remove or clear cache file
            try:
                FALLBACK_CACHE_PATH.unlink(missing_ok=True)
                logger.info("Successfully synced all %d rows and cleared fallback cache.", synced_count)
            except Exception as e:
                logger.warning("Failed to remove fallback cache file: %s", e)

        return synced_count

