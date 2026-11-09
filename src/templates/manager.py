"""Template loader and role-based template resolution."""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent

ROLE_MAPPINGS = [
    (r"sre|reliability", "sre.tex"),
    (r"devops|cloud|infrastructure", "devops.tex"),
    (r"mlops|machine\s*learning", "mlops.tex"),
    (r"data\s*ops|dataops", "data_ops.tex"),
    (r"data\s*engineer", "data_engineer.tex"),
    (r"business\s*analyst|price\s*analyst", "business_analyst.tex"),
    (r"data\s*analyst|analyst", "data_analyst.tex"),
    (r"product\s*manager|product\s*development|technical\s*sales", "tpm.tex"),
]

def get_template_for_role(job_title: str) -> str:
    """Return the raw LaTeX resume template matching the target job title."""
    normalized_title = job_title.lower()
    
    # Check for excluded roles
    if "marketing" in normalized_title or "sales analyst" in normalized_title:
        return ""
        
    for pattern, filename in ROLE_MAPPINGS:
        if re.search(pattern, normalized_title):
            template_path = TEMPLATES_DIR / filename
            if template_path.exists():
                return template_path.read_text(encoding="utf-8")
                
    # Fallback to Software Engineering template
    default_path = TEMPLATES_DIR / "swe.tex"
    return default_path.read_text(encoding="utf-8")

def get_base_cover_letter() -> str:
    """Return the standard base cover letter LaTeX template."""
    cl_path = TEMPLATES_DIR / "cover_letter.tex"
    return cl_path.read_text(encoding="utf-8")