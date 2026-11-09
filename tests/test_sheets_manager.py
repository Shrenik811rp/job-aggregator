"""Unit tests for SheetManager and CSV fallback mechanism."""
import csv
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.sheets.manager import HEADERS, SheetManager


class TestSheetManager(unittest.TestCase):
    """Test suite for SheetManager."""

    def setUp(self):
        self.test_row = [
            "2026-11-07 12:00:00",
            "DevOps Engineer",
            "Dallas, TX",
            "https://jobs.example.com/devops/123",
            "Looking for a skilled DevOps Engineer...",
            "https://drive.google.com/file/d/resume123/view",
            r"\documentclass{article}...",
            "https://drive.google.com/file/d/cl123/view",
            r"\documentclass{article}...",
            "Not Applied",
        ]

    @patch("src.sheets.manager.gspread.service_account")
    def test_init_success(self, mock_service_account):
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_sheet = MagicMock()

        mock_service_account.return_value = mock_client
        mock_client.open.return_value = mock_spreadsheet
        mock_spreadsheet.sheet1 = mock_sheet

        manager = SheetManager()
        self.assertIsNotNone(manager.client)
        self.assertIsNotNone(manager.spreadsheet)
        self.assertIsNotNone(manager.sheet)

    @patch("src.sheets.manager.gspread.service_account")
    def test_init_auth_failure_does_not_crash(self, mock_service_account):
        mock_service_account.side_effect = Exception("Auth connection timed out")

        manager = SheetManager()
        self.assertIsNone(manager.client)
        self.assertIsNone(manager.spreadsheet)
        self.assertIsNone(manager.sheet)

    @patch("src.sheets.manager.gspread.service_account")
    def test_get_processed_links_success(self, mock_service_account):
        mock_client = MagicMock()
        mock_sheet = MagicMock()
        mock_service_account.return_value = mock_client
        mock_client.open.return_value.sheet1 = mock_sheet

        mock_sheet.col_values.return_value = [
            "Job Link",
            "https://jobs.example.com/1",
            "https://jobs.example.com/2",
            "https://jobs.example.com/1",  # duplicate
        ]

        manager = SheetManager()
        links = manager.get_processed_links()
        self.assertEqual(links, {"https://jobs.example.com/1", "https://jobs.example.com/2"})
        mock_sheet.col_values.assert_called_once_with(4)

    @patch("src.sheets.manager.gspread.service_account")
    def test_get_processed_links_empty_sheet(self, mock_service_account):
        mock_client = MagicMock()
        mock_sheet = MagicMock()
        mock_service_account.return_value = mock_client
        mock_client.open.return_value.sheet1 = mock_sheet

        mock_sheet.col_values.return_value = ["Job Link"]

        manager = SheetManager()
        links = manager.get_processed_links()
        self.assertEqual(links, set())

    @patch("src.sheets.manager.gspread.service_account")
    def test_get_processed_links_exception_handled(self, mock_service_account):
        mock_client = MagicMock()
        mock_sheet = MagicMock()
        mock_service_account.return_value = mock_client
        mock_client.open.return_value.sheet1 = mock_sheet

        mock_sheet.col_values.side_effect = Exception("API rate limit exceeded")

        manager = SheetManager()
        links = manager.get_processed_links()
        self.assertEqual(links, set())

    @patch("src.sheets.manager.gspread.service_account")
    def test_get_processed_links_when_client_is_none(self, mock_service_account):
        mock_service_account.side_effect = Exception("No auth")

        manager = SheetManager()
        links = manager.get_processed_links()
        self.assertEqual(links, set())

    @patch("src.sheets.manager.gspread.service_account")
    def test_append_job_row_success(self, mock_service_account):
        mock_client = MagicMock()
        mock_sheet = MagicMock()
        mock_service_account.return_value = mock_client
        mock_client.open.return_value.sheet1 = mock_sheet

        manager = SheetManager()
        result = manager.append_job_row(self.test_row)

        self.assertTrue(result)
        mock_sheet.append_row.assert_called_once_with(self.test_row, value_input_option="USER_ENTERED")

    @patch("src.sheets.manager.FALLBACK_CACHE_PATH")
    @patch("src.sheets.manager.gspread.service_account")
    def test_append_job_row_failure_routes_to_fallback(self, mock_service_account, mock_fallback_path, tmp_path=None):
        mock_client = MagicMock()
        mock_sheet = MagicMock()
        mock_service_account.return_value = mock_client
        mock_client.open.return_value.sheet1 = mock_sheet
        mock_sheet.append_row.side_effect = Exception("Google Sheets 503 Service Unavailable")

        manager = SheetManager()
        with patch.object(manager, "_write_to_fallback_cache") as mock_fallback_write:
            result = manager.append_job_row(self.test_row)
            self.assertFalse(result)
            mock_fallback_write.assert_called_once_with(self.test_row)

    def test_write_to_fallback_cache_creates_file_with_headers(self):
        tmp_csv = Path("test_fallback_cache.csv")
        if tmp_csv.exists():
            tmp_csv.unlink()

        try:
            with patch("src.sheets.manager.FALLBACK_CACHE_PATH", tmp_csv), \
                 patch("src.sheets.manager.gspread.service_account") as mock_sa:
                mock_sa.side_effect = Exception("No auth")
                manager = SheetManager()

                manager._write_to_fallback_cache(self.test_row)

                self.assertTrue(tmp_csv.exists())
                with open(tmp_csv, mode="r", newline="", encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                    self.assertEqual(len(reader), 2)
                    self.assertEqual(reader[0], HEADERS)
                    self.assertEqual(reader[1], self.test_row)

                # Writing a second row should append without repeating headers
                second_row = list(self.test_row)
                second_row[1] = "Data Engineer"
                manager._write_to_fallback_cache(second_row)

                with open(tmp_csv, mode="r", newline="", encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                    self.assertEqual(len(reader), 3)
                    self.assertEqual(reader[0], HEADERS)
                    self.assertEqual(reader[1], self.test_row)
                    self.assertEqual(reader[2], second_row)
        finally:
            if tmp_csv.exists():
                tmp_csv.unlink()

    def test_sync_fallback_cache_success(self):
        tmp_csv = Path("test_fallback_sync.csv")
        if tmp_csv.exists():
            tmp_csv.unlink()

        try:
            with open(tmp_csv, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)
                writer.writerow(self.test_row)
                second_row = list(self.test_row)
                second_row[1] = "Data Analyst"
                writer.writerow(second_row)

            with patch("src.sheets.manager.FALLBACK_CACHE_PATH", tmp_csv), \
                 patch("src.sheets.manager.gspread.service_account") as mock_sa:
                mock_client = MagicMock()
                mock_sheet = MagicMock()
                mock_sa.return_value = mock_client
                mock_client.open.return_value.sheet1 = mock_sheet

                manager = SheetManager()
                synced_count = manager.sync_fallback_cache()

                self.assertEqual(synced_count, 2)
                self.assertEqual(mock_sheet.append_row.call_count, 2)
                # Cache file should be removed on successful upload
                self.assertFalse(tmp_csv.exists())
        finally:
            if tmp_csv.exists():
                tmp_csv.unlink()

    def test_sync_fallback_cache_nonexistent_file(self):
        non_existent = Path("non_existent_cache.csv")
        with patch("src.sheets.manager.FALLBACK_CACHE_PATH", non_existent), \
             patch("src.sheets.manager.gspread.service_account") as mock_sa:
            mock_client = MagicMock()
            mock_sheet = MagicMock()
            mock_sa.return_value = mock_client
            mock_client.open.return_value.sheet1 = mock_sheet

            manager = SheetManager()
            self.assertEqual(manager.sync_fallback_cache(), 0)

    def test_sync_fallback_cache_partial_failure_retains_unrecovered(self):
        tmp_csv = Path("test_fallback_partial.csv")
        if tmp_csv.exists():
            tmp_csv.unlink()

        try:
            row1 = list(self.test_row)
            row1[1] = "Role 1"
            row2 = list(self.test_row)
            row2[1] = "Role 2"

            with open(tmp_csv, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)
                writer.writerow(row1)
                writer.writerow(row2)

            with patch("src.sheets.manager.FALLBACK_CACHE_PATH", tmp_csv), \
                 patch("src.sheets.manager.gspread.service_account") as mock_sa:
                mock_client = MagicMock()
                mock_sheet = MagicMock()
                mock_sa.return_value = mock_client
                mock_client.open.return_value.sheet1 = mock_sheet

                # First call succeeds, second call fails
                mock_sheet.append_row.side_effect = [None, Exception("Rate limit")]

                manager = SheetManager()
                synced_count = manager.sync_fallback_cache()

                self.assertEqual(synced_count, 1)
                self.assertTrue(tmp_csv.exists())

                # The file should now contain HEADERS and row2
                with open(tmp_csv, mode="r", newline="", encoding="utf-8") as f:
                    remaining = list(csv.reader(f))
                    self.assertEqual(len(remaining), 2)
                    self.assertEqual(remaining[0], HEADERS)
                    self.assertEqual(remaining[1], row2)
        finally:
            if tmp_csv.exists():
                tmp_csv.unlink()


if __name__ == "__main__":
    unittest.main()

