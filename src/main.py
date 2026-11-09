"""Master Execution Pipeline for Zero-Cost Job Aggregator and ATS Resume Tailoring Engine."""
from datetime import datetime
import logging
from pathlib import Path
import random
import re
import sys
import time
from typing import Dict, Optional

from src.compiler.pdf_builder import process_document
from src.scrapers.ingest import (
    detect_ats,
    extract_job_description,
    fetch_raw_jobs,
    filter_and_sort_jobs,
)
from src.sheets.manager import SheetManager
from src.tailoring.engine import tailor_application

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("master_pipeline")


def _sanitize_filename(name: str) -> str:
    """Sanitize string for filesystem and Drive safe naming."""
    clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    return re.sub(r'_+', '_', clean).strip('_')


def run_pipeline(limit: Optional[int] = None, enable_delay: bool = True) -> Dict[str, int]:
    """Execute the end-to-end job ingestion, tailoring, compilation, and tracking pipeline.

    Args:
        limit: Optional maximum number of new jobs to process.
        enable_delay: Whether to sleep between job processing iterations.

    Returns:
        Summary metrics dictionary: {"synced_cache": int, "scraped": int, "processed": int, "failed": int}
    """
    metrics = {
        "synced_cache": 0,
        "scraped": 0,
        "processed": 0,
        "failed": 0,
    }

    logger.info("=" * 60)
    logger.info("STARTING MASTER JOB AGGREGATION & RESUME TAILORING PIPELINE")
    logger.info("=" * 60)

    # ---------------------------------------------------------
    # Step 1: Initialize SheetManager & Sync Stranded Cache
    # ---------------------------------------------------------
    logger.info("Step 1: Connecting to Google Sheets and checking local fallback cache...")
    sheet_manager = SheetManager()
    synced_count = sheet_manager.sync_fallback_cache()
    metrics["synced_cache"] = synced_count
    if synced_count > 0:
        logger.info("Synced %d stranded rows from local cache to Google Sheets.", synced_count)
    else:
        logger.info("No stranded rows in local cache.")

    # ---------------------------------------------------------
    # Step 2: Fetch Deduplication Set
    # ---------------------------------------------------------
    logger.info("Step 2: Fetching existing processed job links from Google Sheets...")
    existing_links = sheet_manager.get_processed_links()
    logger.info("Loaded %d previously processed job URLs for deduplication.", len(existing_links))

    # ---------------------------------------------------------
    # Step 3: Ingestion & Filtering Engine
    # ---------------------------------------------------------
    logger.info("Step 3: Ingesting raw jobs from GitHub repositories, RSS feeds, and APIs...")
    raw_jobs = fetch_raw_jobs()
    logger.info("Ingested %d total raw listings across all sources.", len(raw_jobs))

    logger.info("Filtering and prioritizing new jobs (Texas -> US Remote -> Other US)...")
    filtered_jobs = filter_and_sort_jobs(raw_jobs, existing_links)
    metrics["scraped"] = len(filtered_jobs)
    logger.info("Identified %d new eligible jobs matching technical criteria.", len(filtered_jobs))

    if not filtered_jobs:
        logger.info("No new matching jobs to process. Execution complete.")
        return metrics

    # ---------------------------------------------------------
    # Step 4: Apply Execution Limits
    # ---------------------------------------------------------
    if limit and limit > 0:
        logger.info("Step 4: Applying batch limit of %d jobs.", limit)
        jobs_to_process = filtered_jobs[:limit]
    else:
        jobs_to_process = filtered_jobs

    total_jobs = len(jobs_to_process)
    logger.info("Beginning processing of %d jobs...", total_jobs)

    # ---------------------------------------------------------
    # Step 5: Sequential Execution Loop
    # ---------------------------------------------------------
    for idx, job in enumerate(jobs_to_process, start=1):
        job_title = job.get("title", "Software Engineer")
        company = job.get("company", "Company")
        location = job.get("location", "Remote")
        job_url = job.get("url", "")

        logger.info("-" * 60)
        logger.info("[%d/%d] Processing: %s at %s (%s)", idx, total_jobs, job_title, company, location)
        logger.info("Job Link: %s", job_url)

        try:
            # Step 5a: Detect ATS platform
            ats_type = detect_ats(job_url)
            logger.info("Detected ATS platform: %s", ats_type)

            # Step 5b: Extract Job Description
            logger.info("Extracting job description...")
            job_desc = extract_job_description(job_url, ats_type=ats_type, enable_delay=enable_delay)
            if not job_desc or not job_desc.strip():
                job_desc = "Unable to extract job listing"

            # Step 5c: Generate Tailored Documents
            logger.info("Generating ATS-tailored resume and cover letter with Gemini...")
            resume_latex, cl_latex = tailor_application(
                job_title=job_title,
                job_description=job_desc,
                ats_type=ats_type,
            )

            # Step 5d: Clean strings to create safe filenames for the PDFs
            safe_company = _sanitize_filename(company)
            safe_title = _sanitize_filename(job_title)
            resume_filename = f"Resume_{safe_company}_{safe_title}.pdf"
            cl_filename = f"Cover_Letter_{safe_company}_{safe_title}.pdf"

            # Step 5e: Compile & Upload Documents
            logger.info("Compiling LaTeX and uploading documents to Google Drive...")
            resume_pdf_url, final_resume_latex = process_document(resume_latex, resume_filename)
            cl_pdf_url, final_cl_latex = process_document(cl_latex, cl_filename)

            # Step 5f: Prepare 10-column Row Matching Schema
            timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S CDT")
            jd_snippet = job_desc[:1000] if len(job_desc) > 1000 else job_desc

            row_data = [
                timestamp_str,
                job_title,
                location,
                job_url,
                jd_snippet,
                resume_pdf_url,
                final_resume_latex,
                cl_pdf_url,
                final_cl_latex,
                "Not Applied",
            ]

            # Step 5g: Append row to Google Sheets
            logger.info("Appending row to Master Google Sheet...")
            sheet_manager.append_job_row(row_data)
            metrics["processed"] += 1
            logger.info("Successfully recorded job [%d/%d].", idx, total_jobs)

            # Politeness delay between job cycles
            if enable_delay and idx < total_jobs:
                delay = random.uniform(1.5, 4.0)
                logger.info("Sleeping %.2f seconds before next job...", delay)
                time.sleep(delay)

        except Exception as job_err:
            logger.error("Error processing job '%s' at '%s' (%s): %s", job_title, company, job_url, job_err)
            metrics["failed"] += 1

            # Emergency Fallback to guarantee URL is not lost
            try:
                emergency_row = [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S CDT"),
                    job_title,
                    location,
                    job_url,
                    "Unable to extract job listing",
                    "",
                    "",
                    "",
                    "",
                    "Not Applied",
                ]
                sheet_manager.append_job_row(emergency_row)
                logger.info("Emergency fallback row recorded for %s", job_url)
            except Exception as em_err:
                logger.error("Failed to append emergency fallback row: %s", em_err)

            # Never halt: proceed to next job
            continue

    logger.info("=" * 60)
    logger.info("PIPELINE EXECUTION FINISHED")
    logger.info("  - Cached rows synced:   %d", metrics["synced_cache"])
    logger.info("  - Total scraped:        %d", metrics["scraped"])
    logger.info("  - Successfully handled: %d", metrics["processed"])
    logger.info("  - Job failures:         %d", metrics["failed"])
    logger.info("=" * 60)

    return metrics


if __name__ == "__main__":
    run_pipeline()
