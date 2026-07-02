"""
Download the 7 core Bahá'í source texts from reference.bahai.org.
Saves each as texts/{slug}.json with passage-level structure.
Run once — skips files that already exist.
"""

import json
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

OUTPUT = Path(__file__).parent.parent / "texts"
OUTPUT.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "bahAI-Workforce/1.0 (personal spiritual research tool)"}
DELAY = 1.2  # seconds between requests — be polite to the server

TEXTS = [
    {
        "slug": "hidden-words",
        "title": "The Hidden Words",
        "author": "Bahá'u'lláh",
        "url": "https://reference.bahai.org/en/t/b/HW/",
    },
    {
        "slug": "gleanings",
        "title": "Gleanings from the Writings of Bahá'u'lláh",
        "author": "Bahá'u'lláh",
        "url": "https://reference.bahai.org/en/t/b/GWB/",
    },
    {
        "slug": "seven-valleys",
        "title": "The Seven Valleys and the Four Valleys",
        "author": "Bahá'u'lláh",
        "url": "https://reference.bahai.org/en/t/b/SVFV/",
    },
    {
        "slug": "tablets-of-bahaullah",
        "title": "Tablets of Bahá'u'lláh",
        "author": "Bahá'u'lláh",
        "url": "https://reference.bahai.org/en/t/b/TB/",
    },
    {
        "slug": "selections-abdulbaha",
        "title": "Selections from the Writings of 'Abdu'l-Bahá",
        "author": "'Abdu'l-Bahá",
        "url": "https://reference.bahai.org/en/t/ab/SAB/",
    },
    {
        "slug": "some-answered-questions",
        "title": "Some Answered Questions",
        "author": "'Abdu'l-Bahá",
        "url": "https://reference.bahai.org/en/t/ab/SAQ/",
    },
    {
        "slug": "paris-talks",
        "title": "Paris Talks",
        "author": "'Abdu'l-Bahá",
        "url": "https://reference.bahai.org/en/t/ab/PT/",
    },
]

def get_page(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_section_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Find passage-level .html links from a reference.bahai.org index page.
    Excludes index pages, printable versions, and duplicate hrefs.
    """
    domain = urlparse(base_url).netloc
    base_path = urlparse(base_url).path.rstrip("/")
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        p = urlparse(full)
        filename = p.path.split("/")[-1]
        if (
            p.netloc == domain
            and p.path.startswith(base_path + "/")
            and p.path.endswith(".html")
            and "printable" not in filename
            and filename not in ("index.html", "")
            and full not in seen
        ):
            seen.add(full)
            links.append(full)
    return links


def extract_passages(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Pull text from a reference.bahai.org passage page.

    The old reference.bahai.org site stores passage text in:
      div.Stext2     — main passage body
      div.Sitalcent  — italic/invocation lines (e.g. "HE IS THE GLORY OF GLORIES")
      p.StextHead    — section heading  (e.g. "Part I. From the Arabic")
      p.StextHead2   — subsection heading
    """
    current_section = ""

    # Collect section headings first so we know the section for each passage
    for p_tag in soup.find_all("p", class_=lambda c: c and c.startswith("StextHead")):
        t = p_tag.get_text(separator=" ", strip=True)
        if t:
            current_section = t  # last one wins; that's fine per-page

    passages = []

    # Main passage text
    for div in soup.find_all("div", class_="Stext2"):
        text = div.get_text(separator=" ", strip=True)
        if text and len(text) >= 30:
            passages.append({"text": text, "section": current_section, "link": page_url})

    # Italic invocation / epigraph lines (shorter but meaningful)
    for div in soup.find_all("div", class_="Sitalcent"):
        text = div.get_text(separator=" ", strip=True)
        if text and len(text) >= 15:
            passages.append({"text": text, "section": current_section, "link": page_url})

    return passages


def download_text(info: dict):
    out_path = OUTPUT / f"{info['slug']}.json"
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        n = len(existing.get("passages", []))
        print(f"  [skip] {info['slug']}.json already exists ({n} passages) — delete to re-download")
        return

    print(f"\n{'='*60}")
    print(f"  {info['title']}")
    print(f"  {info['url']}")
    print(f"{'='*60}")

    try:
        index_soup = get_page(info["url"])
        time.sleep(DELAY)
    except Exception as e:
        print(f"  ERROR fetching index page: {e}")
        return

    section_links = get_section_links(index_soup, info["url"])
    print(f"  Section links found: {len(section_links)}")

    all_passages = []

    if not section_links:
        # Single-page text or JS-rendered — try extracting from the index page itself
        print("  No section links — extracting from index page directly")
        all_passages = extract_passages(index_soup, info["url"])
    else:
        cap = min(len(section_links), 200)
        for i, link in enumerate(section_links[:cap], 1):
            try:
                soup = get_page(link)
                passages = extract_passages(soup, link)
                all_passages.extend(passages)
                filename = link.split("/")[-1]
                print(f"  [{i:>3}/{cap}] {filename:<25} → {len(passages)} paragraphs")
                time.sleep(DELAY)
            except Exception as e:
                print(f"  ERROR on {link}: {e}")

    if not all_passages:
        print(f"  WARNING: 0 passages extracted — the site may require JavaScript rendering.")
        print(f"  Try opening {info['url']} in a browser and checking if content loads without JS.")

    result = {
        "title": info["title"],
        "author": info["author"],
        "slug": info["slug"],
        "source_url": info["url"],
        "passages": all_passages,
    }
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Saved {len(all_passages)} passages → texts/{info['slug']}.json")


if __name__ == "__main__":
    print("bahAI Workforce — Downloading Bahá'í Source Texts")
    print("Source: Bahá'í Reference Library (reference.bahai.org)")
    print(f"Output: texts/\n")
    for info in TEXTS:
        download_text(info)
    print("\n\nAll done. Next step: python scripts/ingest_texts.py")
