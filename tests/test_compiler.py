"""Unit tests for the subprocess LaTeX compiler and Google Drive PDF uploader."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, call, patch

from src.compiler.pdf_builder import (
    compile_latex_to_pdf,
    process_document,
    upload_pdf_to_drive,
)


class TestCompiler(unittest.TestCase):
    """Test suite for LaTeX compilation and Google Drive upload."""

    def setUp(self):
        self.sample_latex = r"""
        \documentclass{article}
        \begin{document}
        Hello World Test Resume
        \end{document}
        """

    @patch("src.compiler.pdf_builder.subprocess.run")
    def test_compile_latex_to_pdf_success(self, mock_subprocess_run):
        # Mock subprocess.run creating the pdf file in temp_dir
        def fake_run(cmd, *args, **kwargs):
            # Verify nonstopmode and halt-on-error flags
            self.assertIn("-interaction=nonstopmode", cmd)
            self.assertIn("-halt-on-error", cmd)

            # Find the output directory from command arguments
            out_dir_arg = [arg for arg in cmd if arg.startswith("-output-directory=")][0]
            temp_dir = Path(out_dir_arg.split("=", 1)[1])
            pdf_path = temp_dir / "test_doc.pdf"
            pdf_path.write_text("%PDF-1.4 dummy pdf content", encoding="utf-8")

            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "Output written on test_doc.pdf"
            return mock_res

        mock_subprocess_run.side_effect = fake_run

        pdf_result = compile_latex_to_pdf(self.sample_latex, "test_doc.pdf")
        self.assertIsNotNone(pdf_result)
        self.assertTrue(pdf_result.exists())
        self.assertEqual(pdf_result.name, "test_doc.pdf")

        # Cleanup created output file
        if pdf_result.exists():
            pdf_result.unlink()

    @patch("src.compiler.pdf_builder.subprocess.run")
    def test_compile_latex_to_pdf_compiler_error(self, mock_subprocess_run):
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stdout = "! Undefined control sequence: \\badcommand."
        mock_subprocess_run.return_value = mock_res

        pdf_result = compile_latex_to_pdf(self.sample_latex, "failing_doc.pdf")
        self.assertIsNone(pdf_result)

    @patch("src.compiler.pdf_builder.subprocess.run")
    def test_compile_latex_to_pdf_missing_executable(self, mock_subprocess_run):
        mock_subprocess_run.side_effect = FileNotFoundError("pdflatex not found")

        pdf_result = compile_latex_to_pdf(self.sample_latex, "missing_pdflatex.pdf")
        self.assertIsNone(pdf_result)

    @patch("src.compiler.pdf_builder.subprocess.run")
    def test_compile_latex_to_pdf_timeout(self, mock_subprocess_run):
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(cmd="pdflatex", timeout=30)

        pdf_result = compile_latex_to_pdf(self.sample_latex, "timeout_doc.pdf")
        self.assertIsNone(pdf_result)

    def test_compile_latex_to_pdf_empty_code(self):
        self.assertIsNone(compile_latex_to_pdf("", "empty.pdf"))
        self.assertIsNone(compile_latex_to_pdf("   ", "empty.pdf"))

    @patch("src.compiler.pdf_builder.build")
    @patch("src.compiler.pdf_builder.service_account.Credentials.from_service_account_file")
    def test_upload_pdf_to_drive_success(self, mock_creds, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_files = MagicMock()
        mock_permissions = MagicMock()
        mock_service.files.return_value = mock_files
        mock_service.permissions.return_value = mock_permissions

        mock_files.create.return_value.execute.return_value = {
            "id": "file_12345",
        }
        mock_permissions.create.return_value.execute.return_value = {"id": "perm_123"}

        # Create temporary dummy PDF
        dummy_pdf = Path("test_upload.pdf")
        dummy_pdf.write_text("%PDF-1.4 dummy", encoding="utf-8")

        try:
            link = upload_pdf_to_drive(dummy_pdf, "Resume_DevOps.pdf")
            self.assertEqual(link, "https://drive.google.com/uc?export=download&id=file_12345")
            mock_files.create.assert_called_once()
            mock_permissions.create.assert_called_once()
        finally:
            if dummy_pdf.exists():
                dummy_pdf.unlink()

    @patch("src.compiler.pdf_builder.time.sleep")
    @patch("src.compiler.pdf_builder.build")
    @patch("src.compiler.pdf_builder.service_account.Credentials.from_service_account_file")
    def test_upload_pdf_to_drive_retry_then_success(self, mock_creds, mock_build, mock_sleep):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_files = MagicMock()
        mock_permissions = MagicMock()
        mock_service.files.return_value = mock_files
        mock_service.permissions.return_value = mock_permissions

        # Fail on attempt 1, succeed on attempt 2
        mock_files.create.return_value.execute.side_effect = [
            Exception("Transient 503 Service Unavailable"),
            {
                "id": "file_retry_ok",
            },
        ]
        mock_permissions.create.return_value.execute.return_value = {"id": "perm_123"}

        dummy_pdf = Path("test_upload_retry.pdf")
        dummy_pdf.write_text("%PDF-1.4 dummy", encoding="utf-8")

        try:
            link = upload_pdf_to_drive(dummy_pdf, "Resume_Retry.pdf")
            self.assertEqual(link, "https://drive.google.com/uc?export=download&id=file_retry_ok")
            mock_sleep.assert_called_once_with(2.0)
            self.assertEqual(mock_files.create.return_value.execute.call_count, 2)
        finally:
            if dummy_pdf.exists():
                dummy_pdf.unlink()

    @patch("src.compiler.pdf_builder.time.sleep")
    @patch("src.compiler.pdf_builder.build")
    @patch("src.compiler.pdf_builder.service_account.Credentials.from_service_account_file")
    def test_upload_pdf_to_drive_all_retries_fail(self, mock_creds, mock_build, mock_sleep):
        mock_creds.side_effect = Exception("Persistent authentication failure")

        dummy_pdf = Path("test_upload_err.pdf")
        dummy_pdf.write_text("%PDF-1.4 dummy", encoding="utf-8")

        try:
            link = upload_pdf_to_drive(dummy_pdf, "Resume_DevOps.pdf")
            self.assertEqual(link, "")
            self.assertEqual(mock_sleep.call_count, 2)
        finally:
            if dummy_pdf.exists():
                dummy_pdf.unlink()

    def test_upload_pdf_to_drive_nonexistent_file(self):
        non_existent = Path("does_not_exist_12345.pdf")
        link = upload_pdf_to_drive(non_existent, "Test.pdf")
        self.assertEqual(link, "")

    @patch("src.compiler.pdf_builder.upload_pdf_to_drive")
    @patch("src.compiler.pdf_builder.compile_latex_to_pdf")
    def test_process_document_success(self, mock_compile, mock_upload):
        fake_pdf = Path("output/Resume_DevOps_Stripe.pdf")
        mock_compile.return_value = fake_pdf
        mock_upload.return_value = "https://drive.google.com/file/d/abc123/view"

        # Act
        drive_link, latex_code = process_document(self.sample_latex, "Resume_DevOps_Stripe.pdf")

        # Assert
        self.assertEqual(drive_link, "https://drive.google.com/file/d/abc123/view")
        self.assertEqual(latex_code, self.sample_latex)
        mock_compile.assert_called_once_with(self.sample_latex, "Resume_DevOps_Stripe.pdf")
        mock_upload.assert_called_once_with(fake_pdf, "Resume_DevOps_Stripe.pdf")

    @patch("src.compiler.pdf_builder.upload_pdf_to_drive")
    @patch("src.compiler.pdf_builder.compile_latex_to_pdf")
    def test_process_document_compilation_failure(self, mock_compile, mock_upload):
        mock_compile.return_value = None

        drive_link, latex_code = process_document(self.sample_latex, "Resume_Failing.pdf")

        # Must return empty drive link, but preserve raw LaTeX code
        self.assertEqual(drive_link, "")
        self.assertEqual(latex_code, self.sample_latex)
        mock_upload.assert_not_called()

    @patch("src.compiler.pdf_builder.upload_pdf_to_drive")
    @patch("src.compiler.pdf_builder.compile_latex_to_pdf")
    def test_process_document_missing_pdflatex(self, mock_compile, mock_upload):
        mock_compile.return_value = None  # compile_latex_to_pdf returns None when pdflatex is missing

        drive_link, latex_code = process_document(self.sample_latex, "Resume_Missing.pdf")

        self.assertEqual(drive_link, "")
        self.assertEqual(latex_code, self.sample_latex)

    @patch("src.compiler.pdf_builder.upload_pdf_to_drive")
    @patch("src.compiler.pdf_builder.compile_latex_to_pdf")
    def test_process_document_upload_failure(self, mock_compile, mock_upload):
        fake_pdf = Path("output/Resume_Upload_Fail.pdf")
        mock_compile.return_value = fake_pdf
        mock_upload.return_value = ""  # upload failed

        drive_link, latex_code = process_document(self.sample_latex, "Resume_Upload_Fail.pdf")

        self.assertEqual(drive_link, "")
        self.assertEqual(latex_code, self.sample_latex)


if __name__ == "__main__":
    unittest.main()
