"""Subprocess LaTeX compiler and Google Drive PDF uploader."""
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.config import BASE_DIR, CREDENTIALS_FILE_PATH, GOOGLE_DRIVE_FOLDER_ID

logger = logging.getLogger(__name__)

OUTPUT_DIR = BASE_DIR / "output"
MAX_UPLOAD_RETRIES = 3
UPLOAD_RETRY_DELAY = 2.0


def compile_latex_to_pdf(latex_code: str, output_filename: str) -> Optional[Path]:
    """Compile raw LaTeX code into a PDF inside an isolated temporary directory.

    - Writes latex_code to a .tex file.
    - Executes `pdflatex -interaction=nonstopmode -halt-on-error -output-directory={temp_dir} {tex_path}`
      with a 30s timeout to prevent infinite hangs.
    - If successful, copies the generated PDF to project-level `output/` directory.
    - Returns Path to output PDF, or None on failure.
    - Catches FileNotFoundError, TimeoutExpired, and generic exceptions without crashing.
    """
    if not latex_code or not latex_code.strip():
        logger.warning("Empty LaTeX code provided for '%s'. Skipping compilation.", output_filename)
        return None

    base_name = Path(output_filename).stem

    try:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            tex_path = temp_dir / f"{base_name}.tex"
            temp_pdf_path = temp_dir / f"{base_name}.pdf"

            # Write source LaTeX file
            tex_path.write_text(latex_code, encoding="utf-8")

            cmd = [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={temp_dir}",
                str(tex_path),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    "pdflatex compilation failed for '%s' (exit code %d). Output:\n%s",
                    base_name,
                    result.returncode,
                    result.stdout[-500:] if result.stdout else "No compiler stdout",
                )
                return None

            if temp_pdf_path.exists() and temp_pdf_path.stat().st_size > 0:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                final_pdf_path = OUTPUT_DIR / f"{base_name}.pdf"
                shutil.copy2(temp_pdf_path, final_pdf_path)
                logger.info("Successfully compiled and saved PDF to %s", final_pdf_path)
                return final_pdf_path
            else:
                logger.warning("pdflatex returned 0 but PDF was not generated: %s", temp_pdf_path)
                return None

    except FileNotFoundError:
        logger.warning("pdflatex executable not found on system PATH. Skipping PDF generation.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("pdflatex compilation timed out after 30 seconds for '%s'.", base_name)
        return None
    except Exception as e:
        logger.warning("Unexpected error during LaTeX compilation for '%s': %s", base_name, e)
        return None


def upload_pdf_to_drive(pdf_path: Path, display_name: str) -> str:
    """Upload compiled PDF to designated Google Drive folder with 3-attempt retry loop.

    - Authenticates via service account credentials.
    - Retries up to 3 times with a 2-second sleep on network or API failures.
    - Sets permissions to anyone/reader ('Anyone with the link can view').
    - Returns direct 1-click download URL: f"https://drive.google.com/uc?export=download&id={file_id}".
    - If all 3 attempts fail, logs error and safely returns empty string "".
    """
    if not pdf_path or not Path(pdf_path).exists() or Path(pdf_path).stat().st_size == 0:
        logger.warning("PDF path invalid or empty for Drive upload: %s", pdf_path)
        return ""

    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        media = None
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(CREDENTIALS_FILE_PATH),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            file_metadata = {
                "name": display_name,
                "parents": [GOOGLE_DRIVE_FOLDER_ID],
            }

            media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=True)
            file_obj = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
            ).execute()

            file_id = file_obj.get("id")
            if not file_id:
                logger.warning("Drive upload attempt %d returned no file ID for %s", attempt, display_name)
                if attempt < MAX_UPLOAD_RETRIES:
                    time.sleep(UPLOAD_RETRY_DELAY)
                    continue
                return ""

            # Set permission to anyone with link can view
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                ).execute()
            except Exception as perm_err:
                logger.warning("Failed to set public reader permission on Drive file %s: %s", file_id, perm_err)

            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            logger.info("Successfully uploaded %s to Google Drive: %s", display_name, download_url)
            return download_url

        except Exception as e:
            logger.warning(
                "Drive upload attempt %d/%d failed for '%s': %s",
                attempt,
                MAX_UPLOAD_RETRIES,
                display_name,
                e,
            )
            if attempt < MAX_UPLOAD_RETRIES:
                time.sleep(UPLOAD_RETRY_DELAY)
            else:
                logger.error(
                    "All %d upload attempts failed for '%s'. Reverting to empty link.",
                    MAX_UPLOAD_RETRIES,
                    display_name,
                )
                return ""
        finally:
            if media and hasattr(media, "_fd") and media._fd:
                try:
                    media._fd.close()
                except Exception:
                    pass

    return ""


def process_document(latex_code: str, doc_name: str) -> Tuple[str, str]:
    """Execute end-to-end document compilation and Google Drive upload pipeline.

    - Compiles latex_code into PDF via `compile_latex_to_pdf`.
    - Uploads PDF to Google Drive via `upload_pdf_to_drive`.
    - Returns a 2-tuple: (drive_link, latex_code).
    - If compilation or upload fails, drive_link is "", and raw latex_code is preserved for manual recovery.
    """
    drive_link = ""
    try:
        pdf_path = compile_latex_to_pdf(latex_code, doc_name)
        if pdf_path:
            drive_link = upload_pdf_to_drive(pdf_path, doc_name)
        else:
            logger.warning("PDF compilation returned None for '%s'. Preserving raw LaTeX code.", doc_name)
    except Exception as e:
        logger.error("Error during document processing for '%s': %s. Preserving raw LaTeX code.", doc_name, e)
        drive_link = ""

    return drive_link, latex_code
