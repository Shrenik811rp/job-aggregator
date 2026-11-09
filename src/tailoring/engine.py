"""Gemini ATS tailoring engine and LaTeX sanitization layer with XML layout preservation."""
import logging
import re
from typing import Dict, Optional, Tuple

import google.generativeai as genai

from src.config import GEMINI_API_KEY
from src.templates.manager import get_base_cover_letter, get_template_for_role

logger = logging.getLogger(__name__)

PRIMARY_MODEL_NAME = "gemini-2.0-flash"
FALLBACK_MODEL_NAME = "gemini-1.5-flash"

# Configure Google Generative AI
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(PRIMARY_MODEL_NAME)
except Exception as e:
    logger.warning("Failed to initialize primary Gemini model (%s): %s", PRIMARY_MODEL_NAME, e)
    model = None

try:
    fallback_model = genai.GenerativeModel(FALLBACK_MODEL_NAME)
except Exception as e:
    logger.warning("Failed to initialize fallback Gemini model (%s): %s", FALLBACK_MODEL_NAME, e)
    fallback_model = None

# ATS-Specific Persona Definitions
ATS_PERSONAS: Dict[str, str] = {
    "workday": (
        "Act as an expert technical recruiter and systems architect specializing in Workday ATS parsing algorithms. "
        "Workday relies heavily on exact-match keyword strings and contextual skill density. Emphasize exact-match "
        "keywords from the Job Description, dense contextual skill embedding in experience bullets, standard section "
        "headers, and clean bullet formatting with no complex tables or special glyphs."
    ),
    "ashby": (
        "Act as an engineering lead and modern ATS architect specializing in Ashby AI criteria-matching screeners. "
        "Ashby evaluates candidate evidence lines using AI semantic matching. Emphasize direct technical actions paired "
        "specifically with architectural outcomes and quantifiable performance metrics to pass AI screeners."
    ),
    "greenhouse": (
        "Act as an engineering hiring manager and Greenhouse ATS specialist. Greenhouse parsing algorithms emphasize "
        "the Action-Metric-Result formula ([Technical Action] + [Analytical Process/Data Analyzed] + [Quantifiable Impact]), "
        "standard headers, and strong domain keyword alignment."
    ),
    "taleo": (
        "Act as an enterprise talent systems specialist for Oracle Taleo. Taleo requires strict competency model alignment, "
        "direct tool matching, standard ASCII formatting, and unambiguous technical qualifications."
    ),
    "icims": (
        "Act as a recruitment systems specialist for iCIMS. Emphasize the Google XYZ format "
        "('Accomplished [X], as measured by [Y], by doing [Z]'), operational efficiency metrics, and simple, error-free LaTeX formatting."
    ),
    "generic": (
        "Act as a Senior Engineering Manager and hiring leader focusing on scalable systems, clean architecture, "
        "and pragmatic problem-solving. Prioritize clear impact, relevant technical skills, and high-signal engineering contributions."
    ),
}

ANTI_AI_BLACKLIST = [
    "spearheaded",
    "leveraged",
    "testament to",
    "fostered",
    "driving success",
    "proven track record",
    "passionate",
    "dynamic",
    "delve",
    "seamlessly",
    "robust",
    "elevated",
    "transformative",
]


def sanitize_latex(latex_str: str) -> str:
    """Sanitize raw LLM output to ensure pdflatex compilation safety.

    - Strips markdown code blocks.
    - Replaces em-dashes and en-dashes with standard hyphens.
    - Escapes unescaped special characters: &, $, %, _.
    """
    if not latex_str:
        return ""

    # Strip markdown code fencing if present
    code_match = re.search(r'```(?:latex|xml)?\s*(.*?)\s*```', latex_str, re.DOTALL | re.IGNORECASE)
    if code_match:
        latex_str = code_match.group(1).strip()
    else:
        latex_str = re.sub(r'^```(?:latex|xml)?\s*', '', latex_str.strip(), flags=re.IGNORECASE)
        latex_str = re.sub(r'\s*```$', '', latex_str.strip())

    # Replace em-dashes and en-dashes with hyphens
    latex_str = latex_str.replace("—", "-").replace("–", "-")

    # Escape unescaped ampersands: (?<!\\)& -> \&
    latex_str = re.sub(r'(?<!\\)&', r'\&', latex_str)

    # Escape unescaped dollar signs: (?<!\\)\$ -> \$
    latex_str = re.sub(r'(?<!\\)\$', r'\$', latex_str)

    # Escape unescaped percentage signs: (?<!\\)% -> \%
    latex_str = re.sub(r'(?<!\\)%', r'\%', latex_str)

    # Escape unescaped underscores: (?<!\\)_ -> \_
    latex_str = re.sub(r'(?<!\\)_', r'\_', latex_str)

    return latex_str


def _extract_tag(text: str, tag: str) -> Optional[str]:
    """Extract content between XML tags <tag>...</tag> (case-insensitive)."""
    if not text:
        return None
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def _inject_section(doc: str, section_pattern: str, new_content: str) -> str:
    """Surgically replace content of a section up to the next section or end of document."""
    if not new_content or not new_content.strip():
        return doc
    pattern = rf'({section_pattern}\s*\n)(.*?)(\n\s*\\section\*|\n\s*\\end\{{document\}})'
    m = re.search(pattern, doc, re.DOTALL | re.IGNORECASE)
    if not m:
        return doc
    clean = re.sub(rf'^\s*{section_pattern}\s*', '', new_content.strip(), flags=re.IGNORECASE)
    return doc[:m.start(2)] + clean + '\n' + doc[m.end(2):]


def build_tailoring_prompt(
    job_title: str,
    ats_type: str,
    job_description: str,
    base_latex: str,
    doc_type: str = "resume",
) -> str:
    """Build a specialized prompt injecting ATS persona, layout-locking rules, and XML output structure."""
    normalized_ats = ats_type.lower().strip() if ats_type else "generic"
    persona = ATS_PERSONAS.get(normalized_ats, ATS_PERSONAS["generic"])

    blacklist_str = ", ".join(f'"{w}"' for w in ANTI_AI_BLACKLIST)

    if doc_type == "cover_letter":
        task_directive = (
            "STRICT LAYOUT-LOCKING RULES FOR COVER LETTER (NON-NEGOTIABLE):\n"
            "- DO NOT output the full LaTeX document. DO NOT output the preamble, documentclass, candidate contact header, date line, or closing signature block.\n"
            "- Output ONLY the target company name, role, and the tailored body paragraphs wrapped in the following XML tags:\n"
            "  <company>[Target Company Name]</company>\n"
            "  <role>[Target Job Title]</role>\n"
            "  <body_paragraphs>\n"
            "  [The 4 tailored body paragraphs here separated by blank lines]\n"
            "  </body_paragraphs>\n"
            "- The 4 body paragraphs must cover:\n"
            "  * Paragraph 1: Direct application to [Company] for [Role], citing 1-2 core technical alignments.\n"
            "  * Paragraph 2: Core technical achievements and engineering scale mapped to the JD's primary challenge.\n"
            "  * Paragraph 3: Cross-functional collaboration and analytical bridge-building.\n"
            "  * Paragraph 4: Professional closing and call to action.\n"
            "- Do NOT include \\documentclass, \\begin{document}, or \\end{document}."
        )
    else:
        task_directive = (
            "STRICT LAYOUT-LOCKING RULES FOR RESUME (NON-NEGOTIABLE):\n"
            "- DO NOT output the full LaTeX document. DO NOT output the preamble, documentclass, margins, geometry, or candidate contact header.\n"
            "- You must output ONLY the tailored sections wrapped in the following XML tags:\n"
            "  <summary>\n"
            "  [Tailored \\section*{SUMMARY} text here]\n"
            "  </summary>\n"
            "  <experience>\n"
            "  [Tailored \\section*{WORK EXPERIENCE} bullet points here]\n"
            "  </experience>\n"
            "  <projects>\n"
            "  [Tailored \\section*{PROJECT EXPERIENCE} bullet points here]\n"
            "  </projects>\n"
            "  <skills>\n"
            "  [Tailored \\section*{TECHNICAL SKILLS} text here]\n"
            "  </skills>\n"
            "- Rules for content within tags:\n"
            "  1. <summary>: Rewrite the summary paragraph tightening keyword relevance to the target job.\n"
            "  2. <experience>: Rephrase \\item bullet points under WORK EXPERIENCE to match the JD requirements using the specified ATS persona. DO NOT alter company names, role titles, locations, or dates.\n"
            "  3. <projects>: Rephrase \\item bullet points under PROJECT EXPERIENCE to highlight JD-relevant skills and impact.\n"
            "  4. <skills>: Reorder and update tools/skills inside \\section*{TECHNICAL SKILLS} to reflect the JD.\n"
            "- Maintain the EXACT SAME number of bullet points as the base template to guarantee the document stays strictly on 1 page without any layout shift.\n"
            "- Keep LaTeX environment structures (e.g. \\begin{itemize} and \\end{itemize}) intact inside the tags."
        )

    prompt = f"""
{persona}

TARGET JOB TITLE:
{job_title}

TARGET JOB DESCRIPTION:
{job_description}

BASE LATEX TEMPLATE:
{base_latex}

STRICT GUARDRAILS & INSTRUCTIONS:
{task_directive}

ADDITIONAL CONSTRAINTS:
- Anti-AI Blacklist: You are STRICTLY FORBIDDEN from using any of the following buzzwords/phrases:
  {blacklist_str}
- Do NOT use em-dashes (—) or en-dashes (–). Use standard hyphens (-) or commas.
- Ensure all special LaTeX characters (such as &, %, $, _) are appropriately escaped so the output compiles cleanly with pdflatex.
- Return ONLY the XML tags specified above. Do NOT include markdown commentary or explanations outside the tags.
""".strip()

    return prompt


def _generate_with_fallback(prompt: str) -> str:
    """Attempt content generation with primary model, falling back to gemini-1.5-flash on 404/NotFound."""
    global model, fallback_model

    if model is not None:
        try:
            resp = model.generate_content(prompt)
            if hasattr(resp, "text") and resp.text:
                return resp.text
        except Exception as e:
            err_msg = str(e).lower()
            if "404" in err_msg or "not found" in err_msg:
                logger.warning(
                    "Primary model '%s' returned 404/NotFound (%s). Falling back to '%s'.",
                    PRIMARY_MODEL_NAME,
                    e,
                    FALLBACK_MODEL_NAME,
                )
                if fallback_model is None:
                    try:
                        fallback_model = genai.GenerativeModel(FALLBACK_MODEL_NAME)
                    except Exception as fb_init_err:
                        logger.warning("Failed to initialize fallback model %s: %s", FALLBACK_MODEL_NAME, fb_init_err)

                if fallback_model is not None:
                    fb_resp = fallback_model.generate_content(prompt)
                    if hasattr(fb_resp, "text") and fb_resp.text:
                        return fb_resp.text
            else:
                raise e

    if fallback_model is not None:
        fb_resp = fallback_model.generate_content(prompt)
        if hasattr(fb_resp, "text") and fb_resp.text:
            return fb_resp.text

    return ""


def tailor_application(job_title: str, job_description: str, ats_type: str) -> Tuple[str, str]:
    """Tailor resume and cover letter LaTeX templates with strict XML layout preservation.

    - Resolves base templates via templates manager.
    - Fast fallback: if job_description is 'Unable to extract job listing' or empty, returns unmodified templates.
    - Dynamic fallback: attempts primary model (gemini-2.0-flash), falls back to gemini-1.5-flash on 404.
    - Surgical regex injection: extracts XML tags (<summary>, <experience>, <projects>, <skills>, <company>, <role>, <body_paragraphs>)
      and injects them into the base templates, leaving preambles, layouts, and candidate headers 100% untouched.
    - Robust error handling: returns base templates on timeout, quota errors, missing tags, or parsing anomalies.
    - Never raises unhandled exceptions.
    """
    base_resume = get_template_for_role(job_title)
    base_cover_letter = get_base_cover_letter()

    # Fast fallback check
    if (
        not job_description
        or not job_description.strip()
        or job_description.strip() == "Unable to extract job listing"
    ):
        logger.info("Job description unavailable or extraction fallback triggered. Skipping LLM call.")
        return base_resume, base_cover_letter

    if model is None and fallback_model is None:
        logger.warning("Neither primary nor fallback Gemini model is initialized. Reverting to base templates.")
        return base_resume, base_cover_letter

    try:
        # Generate tailored resume sections
        resume_prompt = build_tailoring_prompt(
            job_title=job_title,
            ats_type=ats_type,
            job_description=job_description,
            base_latex=base_resume,
            doc_type="resume",
        )
        raw_resume = _generate_with_fallback(resume_prompt)

        # Extract XML tags for resume
        tag_summary = _extract_tag(raw_resume, "summary")
        tag_experience = _extract_tag(raw_resume, "experience")
        tag_projects = _extract_tag(raw_resume, "projects")
        tag_skills = _extract_tag(raw_resume, "skills")

        # Guardrail: Check if valid sections were returned
        if not tag_experience and not tag_skills and not tag_summary:
            logger.warning("No valid XML section tags found in resume response. Reverting to base resume.")
            tailored_resume = base_resume
        else:
            tailored_resume = base_resume
            if tag_summary:
                tailored_resume = _inject_section(
                    tailored_resume,
                    r"\\section\*\{(?:PROFESSIONAL\s+)?SUMMARY\}",
                    sanitize_latex(tag_summary),
                )
            if tag_experience:
                tailored_resume = _inject_section(
                    tailored_resume,
                    r"\\section\*\{(?:WORK|PROFESSIONAL)\s+EXPERIENCE\}",
                    sanitize_latex(tag_experience),
                )
            if tag_projects:
                tailored_resume = _inject_section(
                    tailored_resume,
                    r"\\section\*\{(?:PROJECT\s+EXPERIENCE|PROJECTS|DATA\s+ENGINEERING\s+PROJECTS)\}",
                    sanitize_latex(tag_projects),
                )
            if tag_skills:
                tailored_resume = _inject_section(
                    tailored_resume,
                    r"\\section\*\{TECHNICAL\s+SKILLS\}",
                    sanitize_latex(tag_skills),
                )

        # Generate tailored cover letter
        cl_prompt = build_tailoring_prompt(
            job_title=job_title,
            ats_type=ats_type,
            job_description=job_description,
            base_latex=base_cover_letter,
            doc_type="cover_letter",
        )
        raw_cl = _generate_with_fallback(cl_prompt)

        # Extract XML tags for cover letter
        tag_company = _extract_tag(raw_cl, "company")
        tag_body = _extract_tag(raw_cl, "body_paragraphs")

        if not tag_body:
            logger.warning("Missing <body_paragraphs> XML tag in cover letter response. Reverting to base cover letter.")
            tailored_cover_letter = base_cover_letter
        else:
            company_name = sanitize_latex(tag_company) if tag_company else "Target Organization"
            tailored_cover_letter = base_cover_letter.replace("GFT", company_name)
            cl_pattern = r"(Dear\s+[^\n]+,\s*\n\s*)(.*?)(\n\s*\\vspace\{10pt\}\s*\n\s*Sincerely,)"
            m = re.search(cl_pattern, tailored_cover_letter, re.DOTALL)
            if m:
                clean_body = sanitize_latex(tag_body)
                tailored_cover_letter = tailored_cover_letter[:m.start(2)] + clean_body + tailored_cover_letter[m.end(2):]
            else:
                logger.warning("Failed to locate body paragraphs insertion point in cover letter template. Reverting to base.")
                tailored_cover_letter = base_cover_letter

        # Final sanity checks: verify output contains documentclass
        if r"\documentclass" not in tailored_resume:
            logger.warning("Tailored resume missing \\documentclass. Reverting to base resume.")
            tailored_resume = base_resume

        if r"\documentclass" not in tailored_cover_letter:
            logger.warning("Tailored cover letter missing \\documentclass. Reverting to base cover letter.")
            tailored_cover_letter = base_cover_letter

        return tailored_resume, tailored_cover_letter

    except Exception as e:
        logger.warning("Gemini tailoring failed (%s). Reverting to base templates.", e)
        return base_resume, base_cover_letter
