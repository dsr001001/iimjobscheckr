"""Extract job listings from a saved iimjobs HTML page and export to Excel.

This script parses loosely structured markup with BeautifulSoup using
heuristics to identify job cards, then extracts core fields (title, company,
location, experience, link) and writes them to an `.xlsx` file.
"""

import sys
import re
from pathlib import Path
from typing import List, Dict, Optional

from bs4 import BeautifulSoup, Tag
import pandas as pd

# Paths for input HTML and output Excel
INPUT_PATH = Path('/workspace/Page_49_htmlr1.htm')
OUTPUT_XLSX = Path('/workspace/jobs_page49.xlsx')


def extract_visible_text(node: Tag) -> str:
    """Return the node's text with whitespace normalized to single spaces."""
    text = node.get_text(" ", strip=True)
    # Normalize whitespace
    return re.sub(r"\s+", " ", text)


def has_class_like(node: Tag, substrings: List[str]) -> bool:
    """Check whether any of the given substrings appear in the node's classes."""
    classes = node.get("class") or []
    # Also check attributes that may encode class-like semantics
    class_attr = " ".join(classes).lower()
    for sub in substrings:
        if sub in class_attr:
            return True
    return False


def find_job_containers(soup: BeautifulSoup) -> List[Tag]:
    """Locate likely job container elements using class/tag heuristics."""
    candidates: List[Tag] = []
    # Consider common container tags
    for tag_name in ["article", "li", "div", "section"]:
        for node in soup.find_all(tag_name):
            if not isinstance(node, Tag):
                continue
            # Heuristics: classes containing job/listing/card/search-result
            if has_class_like(node, ["job", "listing", "result", "card", "opening", "vacancy", "position"]):
                # Avoid extremely generic wrappers by requiring at least one link inside
                if node.find("a"):
                    candidates.append(node)
    # Deduplicate by id
    uniq: List[Tag] = []
    seen = set()
    for n in candidates:
        key = id(n)
        if key not in seen:
            seen.add(key)
            uniq.append(n)
    return uniq


def choose_title(node: Tag) -> Optional[Tag]:
    """Pick the best title element within a container (anchor/heading)."""
    # Prefer heading anchors or headings
    title = None
    # 1) Anchor with /j/ or iimjobs link
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").lower()
        if ("/j/" in href) or ("iimjobs.com" in href and ("/j/" in href or "/job" in href)):
            text = extract_visible_text(a)
            if len(text) >= 5:
                return a
    # 2) Headings within the node
    for h in ["h1", "h2", "h3", "h4"]:
        hnode = node.find(h)
        if hnode:
            text = extract_visible_text(hnode)
            if len(text) >= 5:
                return hnode
    # 3) Fallback: first anchor with meaningful text
    for a in node.find_all("a"):
        text = extract_visible_text(a)
        if len(text) >= 5:
            return a
    return None


def choose_company(node: Tag) -> Optional[str]:
    """Extract company/employer text if present in the container."""
    # Look for elements with class names indicating company
    for el in node.find_all(True):
        if has_class_like(el, ["company", "employer", "org", "firm", "recruiter"]):
            txt = extract_visible_text(el)
            if txt and len(txt) >= 2:
                return txt
    # Heuristic: small text following title
    return None


def choose_location(node: Tag) -> Optional[str]:
    """Extract location text via class hints or inline markers."""
    # Look for elements with class names indicating location
    for el in node.find_all(True):
        if has_class_like(el, ["location", "loc", "city", "place"]):
            txt = extract_visible_text(el)
            if txt and len(txt) >= 2:
                return txt
    # Look for text containing common location markers
    text = extract_visible_text(node)
    m = re.search(r"\b(Location|City)\s*[:|-]\s*([^|\n]+)", text, re.I)
    if m:
        return m.group(2).strip()
    return None


def choose_experience(node: Tag) -> Optional[str]:
    """Extract experience information from the container content."""
    # Common experience markers: 'Exp', 'Experience', 'Years'
    text = extract_visible_text(node)
    m = re.search(r"\b(Exp|Experience)\s*[:|-]?\s*([0-9]+\+?\s*-?\s*[0-9]*\s*years?)", text, re.I)
    if m:
        return m.group(2).strip()
    # Look for class names
    for el in node.find_all(True):
        if has_class_like(el, ["exp", "experience"]):
            txt = extract_visible_text(el)
            if txt:
                return txt
    return None


def choose_link(title_node: Tag) -> Optional[str]:
    """Find the most relevant job link starting from the title node."""
    # If the title node is an anchor, use its href
    if title_node.name == "a" and title_node.has_attr("href"):
        return title_node.get("href")
    # Else, search nearest anchor descendants
    a = title_node.find("a", href=True)
    if a:
        return a.get("href")
    # Else search siblings/parents
    parent = title_node.parent
    if isinstance(parent, Tag):
        a = parent.find("a", href=True)
        if a:
            return a.get("href")
    return None


def absolutize(url: str) -> str:
    """Convert a relative URL to an absolute iimjobs URL."""
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # Handle protocol-relative and site-relative
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.iimjobs.com" + url
    return "https://www.iimjobs.com/" + url.lstrip('/')


def extract_jobs(html_path: Path) -> List[Dict[str, str]]:
    """Parse the HTML file and return a list of job dictionaries."""
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")

    results: List[Dict[str, str]] = []

    containers = find_job_containers(soup)

    # If no containers found, fallback to scanning anchors directly
    if not containers:
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            h = href.lower()
            if "iimjobs.com" in h or h.startswith("/j/") or "/job" in h:
                title_text = extract_visible_text(a)
                results.append({
                    "Title": title_text or "",
                    "Company": "",
                    "Location": "",
                    "Experience": "",
                    "Link": absolutize(href),
                })
        return dedupe_jobs(results)

    for node in containers:
        title_node = choose_title(node)
        if not title_node:
            continue
        title_text = extract_visible_text(title_node)
        link = choose_link(title_node) or ""
        company = choose_company(node) or ""
        location = choose_location(node) or ""
        experience = choose_experience(node) or ""
        if not title_text and not link:
            continue
        results.append({
            "Title": title_text,
            "Company": company,
            "Location": location,
            "Experience": experience,
            "Link": absolutize(link) if link else "",
        })

    return dedupe_jobs(results)


def dedupe_jobs(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Remove duplicate rows based on (title, link) case-insensitive key."""
    seen = set()
    unique_rows: List[Dict[str, str]] = []
    for r in rows:
        key = (r.get("Title", "").lower().strip(), r.get("Link", "").lower().strip())
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(r)
    return unique_rows


def main() -> int:
    """Entry point: read HTML, extract jobs, write Excel, report count."""
    if not INPUT_PATH.exists():
        print(f"Input HTML not found: {INPUT_PATH}", file=sys.stderr)
        return 2

    jobs = extract_jobs(INPUT_PATH)

    if not jobs:
        print("No jobs found. Exporting an empty sheet with headers.")
        df = pd.DataFrame(columns=["Title", "Company", "Location", "Experience", "Link"])
    else:
        df = pd.DataFrame(jobs, columns=["Title", "Company", "Location", "Experience", "Link"])

    # Export to Excel
    OUTPUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Jobs")

    print(f"Wrote {len(df)} rows to {OUTPUT_XLSX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
