"""Retrieve legal primary texts into an external cache without awarding review credit.

Use a low worker count for publisher courtesy. Download errors remain explicit.
No paper or abstract is marked FULL_TEXT_REVIEWED by this program.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import logging
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import urllib.parse


SPECIAL_ARXIV = {"P006": "2410.21845", "P034": "2411.03682", "P038": "2405.10315"}
SPECIAL_PDF = {"P003": "https://proceedings.mlr.press/v15/ross11a/ross11a.pdf",
               "P004": "https://proceedings.mlr.press/v164/hoque22a/hoque22a.pdf",
               "P124": "https://link.springer.com/content/pdf/10.1007/s10514-025-10201-y.pdf",
               "P119": "https://openaccess.thecvf.com/content/ICCV2025/papers/Zhu_IRASim_A_Fine-Grained_World_Model_for_Robot_Manipulation_ICCV_2025_paper.pdf"}


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == "meta" and row.get("content"):
            key = row.get("name", row.get("property", ""))
            self.meta.setdefault(key, []).append(row["content"])


def fetch(url: str, maximum=100 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ExplorerResearch/0.1 (private scientific literature audit)"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Full text exceeds configured download limit")
    return data


def retrieve(paper: dict, cache: Path, evidence_dir: Path) -> dict:
    row = dict(paper)
    paper_id = row["paper_id"]
    if row.get("fulltext_status") == "RETRIEVED":
        return row
    directory = cache / paper_id
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {"paper_id": paper_id, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "legal_source": "public primary author/publisher repository", "review_credit": False}
    chosen_primary = row.get("corrected_primary_url") or row["seed_primary_url"]
    match = re.search(r"arxiv.org/abs/([^/?#]+)", chosen_primary)
    identifier = SPECIAL_ARXIV.get(paper_id, match.group(1) if match else None)
    try:
        primary = "https://arxiv.org/abs/" + identifier if identifier else chosen_primary
        html = fetch(primary).decode("utf-8", "replace")
        (directory / "primary.html").write_text(html)
        parser = MetadataParser()
        parser.feed(html)
        meta = parser.meta
        titles = meta.get("citation_title", meta.get("DC.title", []))
        authors = meta.get("citation_author", meta.get("DC.creator", []))
        dates = meta.get("citation_date", meta.get("citation_publication_date", []))
        row.update(primary_url=primary, title=titles[0] if titles else "", authors="; ".join(authors),
                   year=dates[0][:4] if dates else "", primary_status="PRIMARY_METADATA_RETRIEVED")
        if identifier:
            row["canonical_id"] = "arxiv:" + identifier.split("v")[0]
        urls = meta.get("citation_pdf_url", [])
        pdf_url = SPECIAL_PDF.get(paper_id, "https://arxiv.org/pdf/" + identifier if identifier else urls[0] if urls else "")
        if not pdf_url:
            raise ValueError("No legal primary PDF link found; manual review needed")
        pdf_url = urllib.parse.urljoin(primary, pdf_url)
        evidence.update(primary_url=primary, fulltext_url=pdf_url, primary_metadata=meta)
        data = fetch(pdf_url)
        if not data.startswith(b"%PDF"):
            raise ValueError("Publisher returned non-PDF content")
        digest = hashlib.sha256(data).hexdigest()
        path = directory / "fulltext.pdf"
        path.write_bytes(data)
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = [{"page": i + 1, "text": (page.extract_text() or "").encode("utf-16", "surrogatepass").decode("utf-16", "replace")}
                 for i, page in enumerate(reader.pages)]
        (directory / "pages.json").write_text(json.dumps(pages, ensure_ascii=False))
        (directory / "fulltext.txt").write_text("\n\n".join(f"[PDF PAGE {p['page']}]\n{p['text']}" for p in pages))
        total_chars = sum(len(p["text"]) for p in pages)
        if total_chars < 3000:
            raise ValueError("Text extraction too short; scanned PDF/manual OCR needed")
        row.update(fulltext_status="RETRIEVED", fulltext_url=pdf_url, fulltext_sha256=digest)
        evidence.update(fulltext_sha256=digest, pages=len(pages), extracted_characters=total_chars,
                        cache_key=paper_id + "/fulltext.pdf", extraction="pypdf")
    except Exception as exc:
        row["fulltext_status"] = "RETRIEVAL_FAILED"
        evidence.update(error_type=type(exc).__name__, error=str(exc))
    (evidence_dir / (paper_id + ".json")).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    time.sleep(.6)
    return row


def main() -> None:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path(__file__).parents[1] / "literature_registry.json")
    parser.add_argument("--cache", type=Path, default=Path.home() / "Library/Caches/Explorer/literature")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise SystemExit("Use 1-4 bounded workers")
    document = json.loads(args.registry.read_text())
    selected = [p for p in document["papers"] if not args.ids or p["paper_id"] in args.ids]
    args.cache.mkdir(parents=True, exist_ok=True)
    evidence = args.registry.parent / "evidence"
    evidence.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {p["paper_id"]: executor.submit(retrieve, p, args.cache, evidence) for p in selected}
        for row in document["papers"]:
            if row["paper_id"] in futures:
                updated = futures[row["paper_id"]].result()
                row.update(updated)
                print(row["paper_id"], row["fulltext_status"], row.get("title", "")[:90], flush=True)
                args.registry.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
