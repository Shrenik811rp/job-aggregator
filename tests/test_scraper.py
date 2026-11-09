"""Unit tests for the ingestion engine, ATS detection, filtering, and tiered extractor."""
import unittest
from unittest.mock import MagicMock, patch

from src.scrapers.ingest import (
    detect_ats,
    extract_job_description,
    fetch_raw_jobs,
    filter_and_sort_jobs,
    get_location_rank,
    is_excessive_seniority,
    is_valid_role,
    parse_markdown_tables,
)


class TestScraperIngest(unittest.TestCase):
    """Test suite for scraping, filtering, and ATS extraction engine."""

    def test_parse_markdown_tables(self):
        sample_markdown = """
# Summer 2027 Internships

| Company | Role | Location | Application Link |
| :--- | :--- | :--- | :--- |
| **[Google](https://google.com)** | [SWE Intern](https://google.com/jobs/1) | Mountain View, CA | [Apply](https://google.com/jobs/1) |
| Amazon | SRE Intern | Austin, TX | <a href="https://amazon.jobs/2">Apply</a> |
| Microsoft | Data Analyst Intern | Redmond, WA | https://microsoft.com/jobs/3 |
| Meta | Marketing Specialist | Menlo Park, CA | [Apply](https://meta.com/jobs/4) |
"""
        jobs = parse_markdown_tables(sample_markdown)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[0]["title"], "SWE Intern")
        self.assertEqual(jobs[0]["company"], "Google")
        self.assertEqual(jobs[0]["location"], "Mountain View, CA")
        self.assertEqual(jobs[0]["url"], "https://google.com/jobs/1")

        self.assertEqual(jobs[1]["title"], "SRE Intern")
        self.assertEqual(jobs[1]["company"], "Amazon")
        self.assertEqual(jobs[1]["location"], "Austin, TX")
        self.assertEqual(jobs[1]["url"], "https://amazon.jobs/2")

        self.assertEqual(jobs[2]["title"], "Data Analyst Intern")
        self.assertEqual(jobs[2]["url"], "https://microsoft.com/jobs/3")

    @patch("src.scrapers.ingest.requests.get")
    @patch("src.scrapers.ingest.feedparser.parse")
    def test_fetch_raw_jobs_resilience(self, mock_feedparser, mock_requests_get):
        # Setup mock responses for GitHub, RSS, and APIs
        def side_effect(url, *args, **kwargs):
            mock_resp = MagicMock()
            if "jobright-ai" in url:
                mock_resp.status_code = 404
                mock_resp.text = "Not found"
            elif "SimplifyJobs" in url:
                mock_resp.status_code = 200
                mock_resp.text = "| Company | Role | Location | Link |\n| --- | --- | --- | --- |\n| TestCorp | DevOps Intern | Dallas, TX | [Apply](https://testcorp.com/job) |"
            elif "weworkremotely" in url:
                mock_resp.status_code = 200
                mock_resp.text = "<rss></rss>"
            elif "arbeitnow" in url:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "data": [
                        {
                            "title": "Cloud Engineer",
                            "company_name": "ArbeitCo",
                            "location": "Remote",
                            "url": "https://arbeitnow.com/job/1",
                        }
                    ]
                }
            elif "remoteok" in url:
                mock_resp.status_code = 200
                mock_resp.json.return_value = [
                    {"legal": "Notice"},
                    {
                        "position": "Data Engineer",
                        "company": "RemoteCo",
                        "location": "Remote",
                        "url": "https://remoteok.com/job/2",
                    },
                ]
            else:
                mock_resp.status_code = 500
            return mock_resp

        mock_requests_get.side_effect = side_effect

        mock_feed = MagicMock()
        mock_entry = {
            "title": "WWR Corp: SRE",
            "link": "https://weworkremotely.com/job/3",
            "region": "Remote",
        }
        mock_feed.entries = [mock_entry]
        mock_feedparser.return_value = mock_feed

        raw_jobs = fetch_raw_jobs()
        self.assertIsInstance(raw_jobs, list)
        self.assertGreaterEqual(len(raw_jobs), 4)

        urls = [j["url"] for j in raw_jobs]
        self.assertIn("https://testcorp.com/job", urls)
        self.assertIn("https://arbeitnow.com/job/1", urls)
        self.assertIn("https://remoteok.com/job/2", urls)
        self.assertIn("https://weworkremotely.com/job/3", urls)

    def test_is_valid_role(self):
        # Valid roles
        self.assertTrue(is_valid_role("Software Engineer Intern"))
        self.assertTrue(is_valid_role("SWE New Grad"))
        self.assertTrue(is_valid_role("DevOps Engineer"))
        self.assertTrue(is_valid_role("Site Reliability Engineer"))
        self.assertTrue(is_valid_role("SRE Co-op"))
        self.assertTrue(is_valid_role("Cloud Infrastructure Trainee"))
        self.assertTrue(is_valid_role("Data Analyst"))
        self.assertTrue(is_valid_role("Business Analyst"))
        self.assertTrue(is_valid_role("Price Analyst"))
        self.assertTrue(is_valid_role("Junior Financial Analyst"))  # ends with analyst
        self.assertTrue(is_valid_role("Product Manager"))
        self.assertTrue(is_valid_role("MLOps Engineer"))
        self.assertTrue(is_valid_role("Technical Sales Specialist"))

        # Excluded roles
        self.assertFalse(is_valid_role("Marketing Intern"))
        self.assertFalse(is_valid_role("Product Marketing Manager"))
        self.assertFalse(is_valid_role("Sales Analyst"))
        self.assertFalse(is_valid_role("Senior Sales Analyst"))
        self.assertFalse(is_valid_role("Human Resources Coordinator"))
        self.assertFalse(is_valid_role("Accountant"))

    def test_is_excessive_seniority(self):
        self.assertTrue(is_excessive_seniority("Requires 5+ years of Python experience"))
        self.assertTrue(is_excessive_seniority("Must have 6-8 yrs in Cloud Architecture"))
        self.assertTrue(is_excessive_seniority("Minimum 10+ years required"))
        self.assertFalse(is_excessive_seniority("0-2 years of experience preferred"))
        self.assertFalse(is_excessive_seniority("Requires 1-3 years of work experience"))
        self.assertFalse(is_excessive_seniority("Recent graduates or students graduating in 2027"))

    def test_get_location_rank(self):
        # Rank 1: Texas
        self.assertEqual(get_location_rank("Dallas, TX"), 1)
        self.assertEqual(get_location_rank("Richardson, Texas"), 1)
        self.assertEqual(get_location_rank("Austin, TX (Hybrid)"), 1)
        self.assertEqual(get_location_rank("Houston, TX"), 1)
        self.assertEqual(get_location_rank("Plano, TX"), 1)
        self.assertEqual(get_location_rank("Irving, TX"), 1)
        self.assertEqual(get_location_rank("Fort Worth, TX"), 1)

        # Rank 2: US Remote / Worldwide Remote
        self.assertEqual(get_location_rank("Remote, US"), 2)
        self.assertEqual(get_location_rank("Nationwide Remote"), 2)
        self.assertEqual(get_location_rank("Work From Home"), 2)
        self.assertEqual(get_location_rank("Worldwide (Remote)"), 2)

        # Rank 3: Other US
        self.assertEqual(get_location_rank("Seattle, WA"), 3)
        self.assertEqual(get_location_rank("San Francisco, CA"), 3)
        self.assertEqual(get_location_rank("New York, NY"), 3)

        # Discard non-US unless remote
        self.assertIsNone(get_location_rank("London, UK"))
        self.assertIsNone(get_location_rank("Bengaluru, India"))
        self.assertIsNone(get_location_rank("Berlin, Germany"))
        self.assertIsNone(get_location_rank("Toronto, Canada"))

    def test_filter_and_sort_jobs(self):
        existing_links = {"https://example.com/existing"}
        raw_jobs = [
            # Discarded: existing link
            {"title": "SWE Intern", "location": "Dallas, TX", "url": "https://example.com/existing"},
            # Discarded: invalid role
            {"title": "Marketing Intern", "location": "Dallas, TX", "url": "https://example.com/marketing"},
            # Discarded: excessive seniority
            {
                "title": "DevOps Engineer",
                "location": "Dallas, TX",
                "url": "https://example.com/sr-devops",
                "description": "Must have 7+ years of experience with Kubernetes",
            },
            # Discarded: non-US location
            {"title": "SRE Intern", "location": "London, UK", "url": "https://example.com/uk-sre"},
            # Valid Rank 3: Other US
            {"title": "Data Analyst Intern", "location": "New York, NY", "url": "https://example.com/ny-da"},
            # Valid Rank 1: Texas
            {"title": "Cloud Engineer Intern", "location": "Richardson, TX", "url": "https://example.com/tx-cloud"},
            # Valid Rank 2: Remote
            {"title": "SWE Intern", "location": "US Remote", "url": "https://example.com/remote-swe"},
        ]

        filtered = filter_and_sort_jobs(raw_jobs, existing_links)
        self.assertEqual(len(filtered), 3)

        # Order must strictly be Rank 1 -> Rank 2 -> Rank 3
        self.assertEqual(filtered[0]["url"], "https://example.com/tx-cloud")
        self.assertEqual(filtered[1]["url"], "https://example.com/remote-swe")
        self.assertEqual(filtered[2]["url"], "https://example.com/ny-da")

    def test_detect_ats(self):
        self.assertEqual(detect_ats("https://boards.greenhouse.io/stripe/jobs/1234"), "greenhouse")
        self.assertEqual(detect_ats("https://job-boards.greenhouse.io/databricks/jobs/5678"), "greenhouse")
        self.assertEqual(detect_ats("https://jobs.lever.co/palantir/abcd-1234"), "lever")
        self.assertEqual(detect_ats("https://jobs.ashbyhq.com/anthropic/9876"), "ashby")
        self.assertEqual(detect_ats("https://target.myworkdayjobs.com/careers/job/1"), "workday")
        self.assertEqual(detect_ats("https://careers-bloomberg.icims.com/jobs/2"), "icims")
        self.assertEqual(detect_ats("https://oracle.taleo.net/careersection/job/3"), "taleo")
        self.assertEqual(detect_ats("https://jobs.smartrecruiters.com/Acme/4"), "smartrecruiters")
        self.assertEqual(detect_ats("https://company.bamboohr.com/careers/5"), "bamboohr")
        self.assertEqual(detect_ats("https://careers.google.com/jobs/results/6"), "generic")

    @patch("src.scrapers.ingest.requests.get")
    def test_extract_job_description_greenhouse_tier1(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": "&lt;div&gt;&lt;p&gt;We are hiring a passionate DevOps Engineer with Terraform experience.&lt;/p&gt;&lt;/div&gt;"
        }
        mock_requests_get.return_value = mock_resp

        desc = extract_job_description("https://boards.greenhouse.io/stripe/jobs/12345", enable_delay=False)
        self.assertIn("DevOps Engineer with Terraform experience", desc)

    @patch("src.scrapers.ingest.requests.get")
    def test_extract_job_description_lever_tier1(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "descriptionPlain": "Full stack engineering internship focusing on distributed systems and APIs.",
            "lists": [{"text": "Requirements", "content": "Knowledge of Python and Docker."}],
        }
        mock_requests_get.return_value = mock_resp

        desc = extract_job_description("https://jobs.lever.co/palantir/5678-abcd", enable_delay=False)
        self.assertIn("Full stack engineering internship", desc)
        self.assertIn("Knowledge of Python and Docker", desc)

    @patch("src.scrapers.ingest.requests.get")
    def test_extract_job_description_ashby_tier1(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "jobUrl": "https://jobs.ashbyhq.com/openai/9999",
                    "descriptionPlain": "Join our research engineering team to train and scale multimodal AI models.",
                }
            ]
        }
        mock_requests_get.return_value = mock_resp

        desc = extract_job_description("https://jobs.ashbyhq.com/openai/9999", enable_delay=False)
        self.assertIn("research engineering team", desc)

    @patch("src.scrapers.ingest.requests.get")
    def test_extract_job_description_tier2_soup(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
          <body>
            <article class="job-description">
              <h3>Role Overview</h3>
              <p>We are seeking a Site Reliability Engineer Intern to automate cloud infrastructure and monitor latency.</p>
            </article>
          </body>
        </html>
        """
        mock_requests_get.return_value = mock_resp

        desc = extract_job_description("https://careers.example.com/jobs/sre", ats_type="generic", enable_delay=False)
        self.assertIn("Site Reliability Engineer Intern", desc)

    @patch("src.scrapers.ingest._extract_tier3_playwright")
    @patch("src.scrapers.ingest._extract_tier2_soup")
    def test_extract_job_description_tier3_playwright(self, mock_tier2, mock_tier3):
        mock_tier2.return_value = None
        mock_tier3.return_value = "Rendered job description by Playwright headless browser for Workday."

        desc = extract_job_description("https://target.myworkdayjobs.com/job/1", ats_type="workday", enable_delay=False)
        self.assertIn("Rendered job description by Playwright", desc)

    @patch("src.scrapers.ingest._extract_tier3_playwright")
    @patch("src.scrapers.ingest._extract_tier2_soup")
    def test_extract_job_description_fallback_on_failure(self, mock_tier2, mock_tier3):
        mock_tier2.return_value = None
        mock_tier3.return_value = None

        desc = extract_job_description("https://blocked-cloudflare.com/job/1", ats_type="generic", enable_delay=False)
        self.assertEqual(desc, "Unable to extract job listing")


if __name__ == "__main__":
    unittest.main()

