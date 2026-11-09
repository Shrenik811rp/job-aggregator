"""Unit tests for the QC Validator module (src/tailoring/validator.py)."""
import unittest

from src.tailoring.validator import (
    AI_HALLUCINATION_BLACKLIST,
    extract_numerical_metrics,
    extract_top_jd_keywords,
    validate_tailored_content,
)


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.base_resume = (
            r"\section*{WORK EXPERIENCE}" "\n"
            r"\item Reduced latency by 14\% across distributed systems." "\n"
            r"\item Accelerated throughput by 10\% using caching." "\n"
            r"\item Handled 50GB+ telemetry data daily with 50+ nodes." "\n"
        )
        self.job_desc = (
            "We are seeking a Cloud DevOps Engineer proficient in Kubernetes, Terraform, "
            "Python, AWS, CI/CD, Docker, Linux, and Prometheus. The candidate will design "
            "automated cloud infrastructure and microservices."
        )
        self.valid_extracted_xml = {
            "summary": (
                r"DevOps Engineer specializing in Kubernetes, AWS, and automated CI/CD pipelines."
            ),
            "experience": (
                r"\begin{itemize}" "\n"
                r"\item Accelerated deployments by 10\% using Kubernetes and Docker." "\n"
                r"\item Reduced latency by 14\% across microservices." "\n"
                r"\item Ingested 50GB+ streaming data with Terraform and AWS." "\n"
                r"\end{itemize}"
            ),
            "projects": (
                r"\item Built Prometheus monitoring pipeline on Linux clusters."
            ),
            "skills": (
                r"\textbf{Tools:} Kubernetes, Docker, AWS, Terraform, Python, CI/CD, Linux"
            ),
        }

    def test_validate_tailored_content_success(self):
        result = validate_tailored_content(
            extracted_xml=dict(self.valid_extracted_xml),
            job_desc=self.job_desc,
            base_resume=self.base_resume,
        )
        self.assertTrue(result)

    def test_tag_completeness_missing_tag(self):
        # Missing 'projects'
        incomplete = {
            "summary": "Valid summary",
            "experience": "Valid experience",
            "skills": "Valid skills",
        }
        self.assertFalse(validate_tailored_content(incomplete, self.job_desc, self.base_resume))

    def test_tag_completeness_empty_tag(self):
        # 'summary' is empty or only whitespace
        empty_tag = dict(self.valid_extracted_xml)
        empty_tag["summary"] = "   "
        self.assertFalse(validate_tailored_content(empty_tag, self.job_desc, self.base_resume))

    def test_hallucination_guardrails(self):
        # Blacklisted phrases must trigger validation failure
        test_phrases = ["delve", "spearheaded", "testament to", "transformative", "tapestry"]
        for phrase in test_phrases:
            bad_xml = dict(self.valid_extracted_xml)
            bad_xml["experience"] = f"I {phrase} the Kubernetes infrastructure initiative."
            self.assertFalse(
                validate_tailored_content(bad_xml, self.job_desc, self.base_resume),
                f"Validation should fail when phrase '{phrase}' is present.",
            )

    def test_metric_integrity_success(self):
        # Retains 14%, 10%, 50GB+
        result = validate_tailored_content(
            extracted_xml=dict(self.valid_extracted_xml),
            job_desc=self.job_desc,
            base_resume=self.base_resume,
        )
        self.assertTrue(result)

    def test_metric_integrity_stripped_failure(self):
        # LLM stripped all quantitative metrics
        stripped_xml = dict(self.valid_extracted_xml)
        stripped_xml["experience"] = (
            r"\begin{itemize}" "\n"
            r"\item Accelerated deployments using Kubernetes." "\n"
            r"\item Reduced latency across microservices." "\n"
            r"\item Ingested streaming data with Terraform and AWS." "\n"
            r"\end{itemize}"
        )
        self.assertFalse(validate_tailored_content(stripped_xml, self.job_desc, self.base_resume))

    def test_keyword_alignment_success(self):
        # Over 30% match of top JD keywords
        result = validate_tailored_content(
            extracted_xml=dict(self.valid_extracted_xml),
            job_desc=self.job_desc,
            base_resume=self.base_resume,
        )
        self.assertTrue(result)

    def test_keyword_alignment_low_overlap_failure(self):
        # XML content talking about irrelevant topics with < 30% JD match
        low_overlap_xml = dict(self.valid_extracted_xml)
        low_overlap_xml["summary"] = "Experienced pastry chef baking cakes and desserts."
        low_overlap_xml["experience"] = "Managed daily inventory of flour and sugar with 14% savings and 10% speedup on 50GB+."
        low_overlap_xml["projects"] = "Created a custom wedding cake catalog."
        low_overlap_xml["skills"] = "Baking, Pastry, Confectionery, Fondant"

        self.assertFalse(validate_tailored_content(low_overlap_xml, self.job_desc, self.base_resume))

    def test_latex_safety_auto_escape(self):
        # Unescaped & and % must be auto-escaped when auto_escape=True
        raw_xml = dict(self.valid_extracted_xml)
        raw_xml["summary"] = "Experienced with AT&T systems and 15% latency improvements."
        result = validate_tailored_content(
            extracted_xml=raw_xml,
            job_desc=self.job_desc,
            base_resume=self.base_resume,
            auto_escape=True,
        )
        self.assertTrue(result)
        self.assertIn(r"AT\&T", raw_xml["summary"])
        self.assertIn(r"15\%", raw_xml["summary"])

    def test_latex_safety_fails_when_auto_escape_disabled(self):
        raw_xml = dict(self.valid_extracted_xml)
        raw_xml["summary"] = "Experienced with AT&T systems."
        result = validate_tailored_content(
            extracted_xml=raw_xml,
            job_desc=self.job_desc,
            base_resume=self.base_resume,
            auto_escape=False,
        )
        self.assertFalse(result)

    def test_invalid_input_types(self):
        self.assertFalse(validate_tailored_content(None, self.job_desc, self.base_resume))  # type: ignore
        self.assertFalse(validate_tailored_content("not a dict", self.job_desc, self.base_resume))  # type: ignore

    def test_extract_numerical_metrics(self):
        text = r"Handled 50GB+ data, cut costs by 18.5\% with 10% speedup on 100+ servers."
        metrics = extract_numerical_metrics(text)
        self.assertIn("50GB+", metrics)
        self.assertIn("18.5%", metrics)
        self.assertIn("10%", metrics)
        self.assertIn("100+", metrics)

    def test_extract_top_jd_keywords(self):
        jd = "Kubernetes Docker AWS Python Kubernetes AWS Docker Linux Kubernetes CI/CD"
        keywords = extract_top_jd_keywords(jd, top_n=5)
        self.assertEqual(keywords[0], "kubernetes")
        self.assertIn("docker", keywords)
        self.assertIn("aws", keywords)


if __name__ == "__main__":
    unittest.main()
