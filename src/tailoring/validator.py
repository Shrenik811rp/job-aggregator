"""Quality Control Validator for LLM Tailored Resume Output."""
from collections import Counter
import logging
import re
from typing import Dict, List, Set

from src.tailoring.engine import sanitize_latex

logger = logging.getLogger(__name__)

# Required XML tags for the tailored resume
REQUIRED_RESUME_TAGS = ("summary", "experience", "projects", "skills")

# Generic AI buzzword blacklist
AI_HALLUCINATION_BLACKLIST = [
    "delve",
    "spearheaded",
    "testament to",
    "transformative",
    "tapestry",
    "leveraged",
    "fostered",
    "driving success",
    "proven track record",
    "passionate",
    "dynamic",
    "seamlessly",
    "robust",
    "elevated",
]

# Standard stop words and generic JD words to exclude from keyword extraction
STOP_WORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no",
    "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd",
    "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that",
    "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't",
    "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's",
    "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom",
    "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll",
    "you're", "you've", "your", "yours", "yourself", "yourselves",
    # Common job description boilerplate
    "will", "work", "experience", "role", "team", "skills", "years", "job", "including",
    "opportunity", "candidate", "responsibilities", "requirements", "qualifications",
    "company", "position", "equal", "status", "ability", "working", "preferred", "must",
    "across", "join", "help", "new", "looking", "support", "strong", "well", "plus",
    "etc", "using", "use", "make", "take", "need", "needs", "related",
}


def extract_numerical_metrics(text: str) -> List[str]:
    """Extract quantitative performance metrics from LaTeX or plain text (e.g., '14%', '10%', '50GB+', '50+')."""
    if not text:
        return []
    clean = text.replace(r"\%", "%")
    patterns = [
        r'\b\d+(?:\.\d+)?%',
        r'\b\d+(?:\.\d+)?(?:GB|MB|TB|PB)\+?',
        r'\b\d+\+(?!\w)',
    ]
    metrics = set()
    for pat in patterns:
        for m in re.finditer(pat, clean, re.IGNORECASE):
            metrics.add(m.group(0))
    return sorted(list(metrics))


def extract_top_jd_keywords(job_desc: str, top_n: int = 20) -> List[str]:
    """Extract top N most frequent significant keywords from the job description excluding stop words."""
    if not job_desc:
        return []
    # Tokenize words, preserving tokens like c++, c#, python, aws, etc.
    tokens = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_\-\+\#]{1,}\b', job_desc.lower())
    significant = [t for t in tokens if t not in STOP_WORDS and len(t) >= 3]
    counter = Counter(significant)
    return [word for word, count in counter.most_common(top_n)]


def validate_tailored_content(
    extracted_xml: Dict[str, str],
    job_desc: str,
    base_resume: str,
    auto_escape: bool = True,
) -> bool:
    """Rigorous 5-point Quality Control validation loop for LLM tailored output.

    Validation Categories:
    1. Tag Completeness: Ensure all required XML tags (<summary>, <experience>, <projects>, <skills>) are present and non-empty.
    2. Hallucination Guardrails: Check text against strict blacklist of generic AI phrases.
    3. Metric Integrity: Ensure critical numerical metrics from base_resume survive in tailored output.
    4. Keyword Alignment (JD Match): Ensure >= 30% of top 20 JD keywords appear in tailored text.
    5. LaTeX Safety: Ensure XML content does not contain raw unescaped &, %, $ (auto-escapes or fails).

    Returns:
        True if all checks pass; False otherwise.
    """
    if not isinstance(extracted_xml, dict):
        logger.warning("QC Validation failed: extracted_xml is not a dictionary.")
        return False

    # Check 1: Tag Completeness
    for tag in REQUIRED_RESUME_TAGS:
        val = extracted_xml.get(tag)
        if not val or not isinstance(val, str) or not val.strip():
            logger.warning("QC Validation failed: Missing or empty required XML tag '%s'.", tag)
            return False

    # Check 2: Hallucination Guardrails
    # Inspect <skills> and <experience> (and full XML) for blacklisted AI buzzwords
    combined_exp_skills = (
        extracted_xml.get("experience", "") + " " + extracted_xml.get("skills", "")
    ).lower()
    for phrase in AI_HALLUCINATION_BLACKLIST:
        if re.search(rf'\b{re.escape(phrase.lower())}\b', combined_exp_skills):
            logger.warning("QC Validation failed: Blacklisted AI phrase '%s' detected.", phrase)
            return False

    # Check 5 (Part A): LaTeX Safety (Unescaped &, %, $)
    for tag in REQUIRED_RESUME_TAGS:
        content = extracted_xml[tag]
        if re.search(r'(?<!\\)[&%$]', content):
            if auto_escape:
                extracted_xml[tag] = sanitize_latex(content)
            else:
                logger.warning("QC Validation failed: Raw unescaped LaTeX character in tag '%s'.", tag)
                return False

    # Check 3: Metric Integrity
    base_metrics = extract_numerical_metrics(base_resume)
    if base_metrics:
        tailored_combined = " ".join(
            extracted_xml[t] for t in REQUIRED_RESUME_TAGS
        ).replace(r"\%", "%")
        surviving = [m for m in base_metrics if m.lower() in tailored_combined.lower()]
        # Fail if all metrics were stripped or retention ratio is below 30%
        if len(surviving) == 0 or (len(surviving) / len(base_metrics) < 0.30):
            logger.warning(
                "QC Validation failed: Metric integrity failure. Base metrics (%d): %s; Surviving (%d): %s",
                len(base_metrics), base_metrics, len(surviving), surviving
            )
            return False

    # Check 4: Keyword Alignment (JD Match)
    top_keywords = extract_top_jd_keywords(job_desc, top_n=20)
    if top_keywords:
        tailored_combined_lower = " ".join(
            extracted_xml[t] for t in REQUIRED_RESUME_TAGS
        ).lower()
        matched = [
            kw for kw in top_keywords
            if re.search(rf'\b{re.escape(kw)}\b', tailored_combined_lower)
        ]
        overlap_ratio = len(matched) / len(top_keywords)
        if overlap_ratio < 0.30:
            logger.warning(
                "QC Validation failed: Keyword alignment too low (%d/%d = %.1f%% < 30%%). Top JD keywords: %s, Matched: %s",
                len(matched), len(top_keywords), overlap_ratio * 100, top_keywords, matched
            )
            return False

    # Check 5 (Part B): Confirm no unescaped syntax breakers remain
    for tag in REQUIRED_RESUME_TAGS:
        if re.search(r'(?<!\\)[&%$]', extracted_xml[tag]):
            logger.warning("QC Validation failed: Unescaped LaTeX characters remain in '%s'.", tag)
            return False

    return True
