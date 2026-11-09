"""Unit tests for the Gemini ATS tailoring engine and LaTeX sanitization layer with XML layout preservation."""
import unittest
from unittest.mock import MagicMock, patch

from src.tailoring.engine import (
    ANTI_AI_BLACKLIST,
    ATS_PERSONAS,
    _extract_tag,
    _inject_section,
    build_tailoring_prompt,
    sanitize_latex,
    tailor_application,
)
from src.templates.manager import get_base_cover_letter, get_template_for_role


class TestTailoringEngine(unittest.TestCase):
    """Test suite for LaTeX sanitization, layout-locked prompts, and model fallbacks."""

    def test_sanitize_latex_special_characters(self):
        # Raw string with unescaped and escaped characters
        raw = r"Earned $100K at AT&T with 15% growth in my_module. Already escaped: \$50, \&, 20\%, and my\_func."
        sanitized = sanitize_latex(raw)

        # Unescaped should be escaped
        self.assertIn(r"\$100K", sanitized)
        self.assertIn(r"AT\&T", sanitized)
        self.assertIn(r"15\%", sanitized)
        self.assertIn(r"my\_module", sanitized)

        # Pre-escaped must NOT be double-escaped
        self.assertIn(r"\$50", sanitized)
        self.assertNotIn(r"\\$50", sanitized)
        self.assertIn(r"\&", sanitized)
        self.assertNotIn(r"\\&", sanitized)
        self.assertIn(r"20\%", sanitized)
        self.assertNotIn(r"\\20%", sanitized)
        self.assertIn(r"my\_func", sanitized)
        self.assertNotIn(r"my\\_func", sanitized)

    def test_sanitize_latex_dashes(self):
        raw = "Kubernetes—Docker–Terraform"
        sanitized = sanitize_latex(raw)
        self.assertEqual(sanitized, "Kubernetes-Docker-Terraform")

    def test_sanitize_latex_markdown_code_fences(self):
        fenced_latex = "```latex\n\\documentclass{article}\n\\begin{document}\nHello World\n\\end{document}\n```"
        sanitized = sanitize_latex(fenced_latex)
        self.assertFalse(sanitized.startswith("```"))
        self.assertFalse(sanitized.endswith("```"))
        self.assertTrue(sanitized.startswith(r"\documentclass{article}"))
        self.assertTrue(sanitized.endswith(r"\end{document}"))

    def test_extract_tag(self):
        xml_text = "<summary>Tailored summary</summary><EXPERIENCE>Bullet 1</EXPERIENCE>"
        self.assertEqual(_extract_tag(xml_text, "summary"), "Tailored summary")
        self.assertEqual(_extract_tag(xml_text, "experience"), "Bullet 1")
        self.assertIsNone(_extract_tag(xml_text, "nonexistent"))
        self.assertIsNone(_extract_tag("", "summary"))

    def test_inject_section(self):
        doc = "\\section*{SUMMARY}\nOld summary\n\n\\section*{EDUCATION}\nOld education\n"
        # Normal injection
        new_doc = _inject_section(doc, r"\\section\*\{SUMMARY\}", "New summary")
        self.assertIn("New summary", new_doc)
        self.assertNotIn("Old summary", new_doc)
        self.assertIn("\\section*{EDUCATION}", new_doc)

        # Injection with redundant header
        new_doc_redundant = _inject_section(doc, r"\\section\*\{SUMMARY\}", "\\section*{SUMMARY}\nNew summary 2")
        self.assertIn("New summary 2", new_doc_redundant)
        self.assertEqual(new_doc_redundant.count("\\section*{SUMMARY}"), 1)

        # Missing section leaves doc intact
        untouched = _inject_section(doc, r"\\section\*\{PROJECTS\}", "New projects")
        self.assertEqual(untouched, doc)

    def test_build_tailoring_prompt_resume_layout_locking(self):
        for ats in ["workday", "ashby", "greenhouse", "taleo", "icims", "generic"]:
            prompt = build_tailoring_prompt(
                job_title="DevOps Engineer",
                ats_type=ats,
                job_description="We need Terraform and AWS experts.",
                base_latex=r"\documentclass{article}",
                doc_type="resume",
            )
            # Persona directive
            self.assertIn(ATS_PERSONAS[ats], prompt)
            # Anti-AI blacklist words
            for word in ANTI_AI_BLACKLIST:
                self.assertIn(word, prompt)
            # XML Layout locking constraints
            self.assertIn("STRICT LAYOUT-LOCKING RULES FOR RESUME", prompt)
            self.assertIn("DO NOT output the full LaTeX document", prompt)
            self.assertIn("<summary>", prompt)
            self.assertIn("<experience>", prompt)
            self.assertIn("<projects>", prompt)
            self.assertIn("<skills>", prompt)
            self.assertIn("Maintain the EXACT SAME number of bullet points", prompt)

    def test_build_tailoring_prompt_cover_letter_layout_locking(self):
        prompt = build_tailoring_prompt(
            job_title="DevOps Engineer",
            ats_type="workday",
            job_description="We need Terraform and AWS experts.",
            base_latex=r"\documentclass{letter}",
            doc_type="cover_letter",
        )
        self.assertIn("STRICT LAYOUT-LOCKING RULES FOR COVER LETTER", prompt)
        self.assertIn("DO NOT output the full LaTeX document", prompt)
        self.assertIn("<company>", prompt)
        self.assertIn("<role>", prompt)
        self.assertIn("<body_paragraphs>", prompt)
        self.assertIn("Paragraph 1: Direct application", prompt)
        self.assertIn("Paragraph 2: Core technical achievements", prompt)
        self.assertIn("Paragraph 3: Cross-functional collaboration", prompt)
        self.assertIn("Paragraph 4: Professional closing", prompt)

    @patch("src.tailoring.engine.model")
    def test_tailor_application_success(self, mock_model):
        mock_resume_resp = MagicMock()
        mock_resume_resp.text = (
            "<summary>\n"
            r"Tailored for AT&T with 15% efficiency." + "\n"
            "</summary>\n"
            "<experience>\n"
            r"\noindent\small \textbf{o9 Solutions, Inc.} \hfill Jan 2023 to Aug 2025 \\" + "\n"
            r"\textit{Software & Infrastructure Systems Engineer} \hfill \textit{Remote}" + "\n"
            r"\begin{itemize}" + "\n"
            r"\item Accelerated partner onboarding at AT&T with 15% speedup." + "\n"
            r"\end{itemize}" + "\n"
            "</experience>\n"
            "<skills>\n"
            r"\noindent\small \textbf{Programming Languages:} Python, Go, C++" + "\n"
            "</skills>"
        )

        mock_cl_resp = MagicMock()
        mock_cl_resp.text = (
            "<company>Google</company>\n"
            "<role>Cloud DevOps Engineer</role>\n"
            "<body_paragraphs>\n"
            "Paragraph 1 applying to Google for Cloud DevOps Engineer.\n\n"
            "Paragraph 2 highlighting scale and 15% latency reduction.\n\n"
            "Paragraph 3 detailing cross-functional collaboration.\n\n"
            "Paragraph 4 concluding." + "\n"
            "</body_paragraphs>"
        )

        mock_model.generate_content.side_effect = [mock_resume_resp, mock_cl_resp]

        resume, cl = tailor_application(
            job_title="DevOps Engineer",
            job_description="Seeking a Cloud DevOps Engineer with Kubernetes.",
            ats_type="workday",
        )

        # Ensure content was tailored and properly sanitized
        self.assertIn(r"\documentclass", resume)
        self.assertIn(r"AT\&T", resume)
        self.assertIn(r"15\%", resume)
        self.assertIn("SHRENIK PILLAI", resume)
        self.assertIn("EDUCATION", resume)

        self.assertIn(r"\documentclass", cl)
        self.assertIn("Google \\", cl)
        self.assertIn("Dear Google Team,", cl)
        self.assertIn("Paragraph 1 applying to Google", cl)
        self.assertIn("Shrenik Pillai", cl)

    @patch("src.tailoring.engine.fallback_model")
    @patch("src.tailoring.engine.model")
    def test_tailor_application_model_404_fallback(self, mock_model, mock_fallback_model):
        # Primary model returns 404 NotFound
        mock_model.generate_content.side_effect = Exception("404 models/gemini-2.0-flash is not found for API version v1beta")

        # Fallback model succeeds with XML tags
        mock_fb_resume = MagicMock()
        mock_fb_resume.text = (
            "<summary>\nTailored resume via fallback model.\n</summary>\n"
            "<skills>\nTechnical skills via fallback.\n</skills>"
        )
        mock_fb_cl = MagicMock()
        mock_fb_cl.text = (
            "<company>Amazon</company>\n"
            "<body_paragraphs>\nTailored cover letter via fallback model.\n</body_paragraphs>"
        )
        mock_fallback_model.generate_content.side_effect = [mock_fb_resume, mock_fb_cl]

        resume, cl = tailor_application(
            job_title="DevOps Engineer",
            job_description="Seeking a Cloud DevOps Engineer.",
            ats_type="greenhouse",
        )

        self.assertIn("Tailored resume via fallback model", resume)
        self.assertIn("Dear Amazon Team,", cl)
        self.assertIn("Tailored cover letter via fallback model", cl)
        self.assertEqual(mock_fallback_model.generate_content.call_count, 2)

    @patch("src.tailoring.engine.model")
    def test_tailor_application_fast_fallback_on_empty_jd(self, mock_model):
        base_resume = get_template_for_role("DevOps Engineer")
        base_cl = get_base_cover_letter()

        # Fast fallback when JD is 'Unable to extract job listing'
        res1, cl1 = tailor_application(
            job_title="DevOps Engineer",
            job_description="Unable to extract job listing",
            ats_type="workday",
        )
        self.assertEqual(res1, base_resume)
        self.assertEqual(cl1, base_cl)
        mock_model.generate_content.assert_not_called()

        # Fast fallback when JD is empty string
        res2, cl2 = tailor_application(
            job_title="DevOps Engineer",
            job_description="",
            ats_type="workday",
        )
        self.assertEqual(res2, base_resume)
        self.assertEqual(cl2, base_cl)
        mock_model.generate_content.assert_not_called()

    @patch("src.tailoring.engine.fallback_model")
    @patch("src.tailoring.engine.model")
    def test_tailor_application_api_exception_fallback(self, mock_model, mock_fb_model):
        base_resume = get_template_for_role("Site Reliability Engineer")
        base_cl = get_base_cover_letter()

        # Non-404 exception (e.g. 429 quota exceeded)
        mock_model.generate_content.side_effect = Exception("Google API 429: Quota exceeded")

        res, cl = tailor_application(
            job_title="Site Reliability Engineer",
            job_description="Need an SRE to manage clusters.",
            ats_type="greenhouse",
        )

        # Must gracefully catch exception and return base templates without crashing
        self.assertEqual(res, base_resume)
        self.assertEqual(cl, base_cl)
        mock_fb_model.generate_content.assert_not_called()

    @patch("src.tailoring.engine.model")
    def test_tailor_application_malformed_output_fallback(self, mock_model):
        base_resume = get_template_for_role("Data Analyst")
        base_cl = get_base_cover_letter()

        mock_resume_resp = MagicMock()
        mock_resume_resp.text = "Here is some markdown text without any xml tags."
        mock_cl_resp = MagicMock()
        mock_cl_resp.text = "Just plain text cover letter."

        mock_model.generate_content.side_effect = [mock_resume_resp, mock_cl_resp]

        res, cl = tailor_application(
            job_title="Data Analyst",
            job_description="Looking for SQL and Tableau analyst.",
            ats_type="ashby",
        )

        # Should fall back to base templates due to missing XML section tags
        self.assertEqual(res, base_resume)
        self.assertEqual(cl, base_cl)

    @patch("src.tailoring.engine.model")
    def test_tailor_application_preserves_preamble_and_layout(self, mock_model):
        base_resume = get_template_for_role("Software Engineer")
        base_cl = get_base_cover_letter()

        mock_resume_resp = MagicMock()
        mock_resume_resp.text = "<summary>Tailored summary.</summary><skills>Tailored skills.</skills>"
        mock_cl_resp = MagicMock()
        mock_cl_resp.text = "<company>Meta</company><body_paragraphs>Paragraph 1.\n\nParagraph 2.</body_paragraphs>"

        mock_model.generate_content.side_effect = [mock_resume_resp, mock_cl_resp]

        resume, cl = tailor_application(
            job_title="Software Engineer",
            job_description="Software Engineer role.",
            ats_type="generic",
        )

        # Preamble up to \begin{document} must be 100% identical
        base_resume_preamble = base_resume[: base_resume.find(r"\begin{document}")]
        tailored_resume_preamble = resume[: resume.find(r"\begin{document}")]
        self.assertEqual(base_resume_preamble, tailored_resume_preamble)

        base_cl_preamble = base_cl[: base_cl.find(r"\begin{document}")]
        tailored_cl_preamble = cl[: cl.find(r"\begin{document}")]
        self.assertEqual(base_cl_preamble, tailored_cl_preamble)


if __name__ == "__main__":
    unittest.main()
