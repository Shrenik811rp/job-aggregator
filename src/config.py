"""Configuration loader and environment validator."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory paths
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
CREDENTIALS_FILE_PATH = BASE_DIR / "credentials.json"
FALLBACK_CACHE_PATH = BASE_DIR / "fallback_cache.csv"

# Load local environment variables
load_dotenv(dotenv_path=ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Job_Application_Tracker")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

# Validation guardrails
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing from .env file.")
if not GOOGLE_DRIVE_FOLDER_ID:
    raise ValueError("GOOGLE_DRIVE_FOLDER_ID is missing from .env file.")
if not CREDENTIALS_FILE_PATH.exists():
    raise FileNotFoundError(f"Missing service account file at: {CREDENTIALS_FILE_PATH}")