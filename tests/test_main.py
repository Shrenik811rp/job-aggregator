"""Unit tests for the master execution loop and CLI entrypoint."""
import unittest
from unittest.mock import MagicMock, call, patch

from main import main
from src.main import run_pipeline


class TestMainPipeline(unittest.TestCase):
    """Test suite for master pipeline orchestration, error resilience, and CLI limits."""

    def setUp(self):
        self.sample_jobs = [
            {
                "title": "DevOps Engineer",
                "company": "Stripe",
                "location": "Dallas, TX",
                "url": "https://stripe.com/jobs/1",
            },
            {
                "title": "Site Reliability Engineer",
                "company": "Datadog",
                "location": "Remote",
                "url": "https://datadog.com/jobs/2",
            },
            {
                "title": "Data Analyst",
                "company": "Airbnb",
                "location": "San Francisco, CA",
                "url": "https://airbnb.com/jobs/3",
            },
        ]

    @patch("src.main.SheetManager")
    @patch("src.main.fetch_raw_jobs")
    @patch("src.main.filter_and_sort_jobs")
    @patch("src.main.extract_job_description")
    @patch("src.main.tailor_application")
    @patch("src.main.process_document")
    def test_run_pipeline_success(
        self,
        mock_process_doc,
        mock_tailor,
        mock_extract_jd,
        mock_filter_sort,
        mock_fetch_raw,
        mock_sheet_manager_cls,
    ):
        mock_sm = MagicMock()
        mock_sheet_manager_cls.return_value = mock_sm
        mock_sm.sync_fallback_cache.return_value = 1
        mock_sm.get_processed_links.return_value = set()

        mock_fetch_raw.return_value = self.sample_jobs[:2]
        mock_filter_sort.return_value = self.sample_jobs[:2]
        mock_extract_jd.return_value = "Detailed Job Description for testing."
        mock_tailor.return_value = (r"\documentclass{resume}", r"\documentclass{letter}")
        mock_process_doc.side_effect = [
            ("https://drive.google.com/resume.pdf", r"\documentclass{resume}"),
            ("https://drive.google.com/cl.pdf", r"\documentclass{letter}"),
            ("https://drive.google.com/resume2.pdf", r"\documentclass{resume}"),
            ("https://drive.google.com/cl2.pdf", r"\documentclass{letter}"),
        ]

        # Execute pipeline
        metrics = run_pipeline(enable_delay=False)

        # Verify metrics
        self.assertEqual(metrics["synced_cache"], 1)
        self.assertEqual(metrics["scraped"], 2)
        self.assertEqual(metrics["processed"], 2)
        self.assertEqual(metrics["failed"], 0)

        # Verify Google Sheets appends
        self.assertEqual(mock_sm.append_job_row.call_count, 2)
        first_row = mock_sm.append_job_row.call_args_list[0][0][0]
        self.assertEqual(len(first_row), 10)
        self.assertEqual(first_row[1], "DevOps Engineer")
        self.assertEqual(first_row[2], "Dallas, TX")
        self.assertEqual(first_row[3], "https://stripe.com/jobs/1")
        self.assertEqual(first_row[5], "https://drive.google.com/resume.pdf")
        self.assertEqual(first_row[7], "https://drive.google.com/cl.pdf")
        self.assertEqual(first_row[9], "Not Applied")

    @patch("src.main.SheetManager")
    @patch("src.main.fetch_raw_jobs")
    @patch("src.main.filter_and_sort_jobs")
    @patch("src.main.extract_job_description")
    @patch("src.main.tailor_application")
    @patch("src.main.process_document")
    def test_run_pipeline_loop_resilience_on_failure(
        self,
        mock_process_doc,
        mock_tailor,
        mock_extract_jd,
        mock_filter_sort,
        mock_fetch_raw,
        mock_sheet_manager_cls,
    ):
        mock_sm = MagicMock()
        mock_sheet_manager_cls.return_value = mock_sm
        mock_sm.sync_fallback_cache.return_value = 0
        mock_sm.get_processed_links.return_value = set()

        mock_fetch_raw.return_value = self.sample_jobs
        mock_filter_sort.return_value = self.sample_jobs

        # Job 1 succeeds, Job 2 fails with fatal exception, Job 3 succeeds
        mock_extract_jd.side_effect = [
            "JD 1 text",
            RuntimeError("Unexpected connection reset!"),
            "JD 3 text",
        ]
        mock_tailor.return_value = (r"\documentclass{resume}", r"\documentclass{letter}")
        mock_process_doc.return_value = ("https://drive.google.com/file.pdf", r"\documentclass{...}")

        metrics = run_pipeline(enable_delay=False)

        # Verify loop did NOT halt: processed 2, failed 1
        self.assertEqual(metrics["scraped"], 3)
        self.assertEqual(metrics["processed"], 2)
        self.assertEqual(metrics["failed"], 1)

        # 2 standard job rows + 1 emergency fallback row = 3 appends total
        self.assertEqual(mock_sm.append_job_row.call_count, 3)

        # Verify job 3 was still processed despite job 2 failing
        last_row = mock_sm.append_job_row.call_args_list[2][0][0]
        self.assertEqual(last_row[1], "Data Analyst")
        self.assertEqual(last_row[3], "https://airbnb.com/jobs/3")

    @patch("src.main.SheetManager")
    @patch("src.main.fetch_raw_jobs")
    @patch("src.main.filter_and_sort_jobs")
    @patch("src.main.extract_job_description")
    @patch("src.main.tailor_application")
    @patch("src.main.process_document")
    def test_run_pipeline_with_limit(
        self,
        mock_process_doc,
        mock_tailor,
        mock_extract_jd,
        mock_filter_sort,
        mock_fetch_raw,
        mock_sheet_manager_cls,
    ):
        mock_sm = MagicMock()
        mock_sheet_manager_cls.return_value = mock_sm
        mock_sm.sync_fallback_cache.return_value = 0
        mock_sm.get_processed_links.return_value = set()

        mock_fetch_raw.return_value = self.sample_jobs
        mock_filter_sort.return_value = self.sample_jobs
        mock_extract_jd.return_value = "JD text"
        mock_tailor.return_value = (r"\documentclass{resume}", r"\documentclass{letter}")
        mock_process_doc.return_value = ("https://drive.google.com/file.pdf", r"\documentclass{...}")

        # Limit to 1 job
        metrics = run_pipeline(limit=1, enable_delay=False)

        self.assertEqual(metrics["scraped"], 3)
        self.assertEqual(metrics["processed"], 1)
        self.assertEqual(mock_sm.append_job_row.call_count, 1)

    @patch("src.main.SheetManager")
    @patch("src.main.fetch_raw_jobs")
    @patch("src.main.filter_and_sort_jobs")
    def test_run_pipeline_empty_jobs(self, mock_filter_sort, mock_fetch_raw, mock_sheet_manager_cls):
        mock_sm = MagicMock()
        mock_sheet_manager_cls.return_value = mock_sm
        mock_sm.sync_fallback_cache.return_value = 0
        mock_sm.get_processed_links.return_value = set()

        mock_fetch_raw.return_value = []
        mock_filter_sort.return_value = []

        metrics = run_pipeline(enable_delay=False)
        self.assertEqual(metrics["scraped"], 0)
        self.assertEqual(metrics["processed"], 0)
        mock_sm.append_job_row.assert_not_called()

    @patch("main.run_pipeline")
    @patch("main.argparse.ArgumentParser.parse_args")
    def test_cli_main_limit_flag(self, mock_parse_args, mock_run_pipeline):
        args = MagicMock()
        args.limit = 5
        args.sync_only = False
        mock_parse_args.return_value = args

        main()
        mock_run_pipeline.assert_called_once_with(limit=5)

    @patch("main.SheetManager")
    @patch("main.argparse.ArgumentParser.parse_args")
    def test_cli_main_sync_only_flag(self, mock_parse_args, mock_sheet_manager_cls):
        mock_sm = MagicMock()
        mock_sheet_manager_cls.return_value = mock_sm
        mock_sm.sync_fallback_cache.return_value = 3

        args = MagicMock()
        args.limit = None
        args.sync_only = True
        mock_parse_args.return_value = args

        with self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        mock_sm.sync_fallback_cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()

