#!/usr/bin/env python3
"""
mineru_client.py — Standalone MinerU cloud PDF parser (API v4)

Mirrors the upload/poll/extract flow from llm-for-zotero/src/utils/mineruClient.ts.
PDFs longer than --max-pages are split into segments, parsed separately,
then auto-merged into a single output folder.

See README.md for full usage, parameters, and output structure.
"""

import argparse
import json
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import re

import requests

# ---------------------------------------------------------------------------
# Project-level paths
# ---------------------------------------------------------------------------

PROJECT_DIR        = Path(__file__).parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"

# ---------------------------------------------------------------------------
# pypdf is required for page-count detection and auto-segmentation.
# ---------------------------------------------------------------------------
try:
    from pypdf import PdfReader as _PdfReader          # pypdf >= 3.x
except ImportError:
    try:
        from PyPDF2 import PdfReader as _PdfReader      # legacy alias (older installs)
    except ImportError:
        raise ImportError(
            "pypdf is required but not installed.\n"
            "Run:  pip install pypdf"
        ) from None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MINERU_API_BASE      = "https://mineru.net/api/v4"
POLL_INTERVAL_S      = 3
DEFAULT_POLL_TIMEOUT = 30 * 60   # 30 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Keep only printable ASCII — mirrors getSafePdfFileName() in mineruClient.ts."""
    safe = "".join(c if 0x20 <= ord(c) <= 0x7E else "_" for c in name)
    return safe or "paper.pdf"


def get_pdf_page_count(pdf_path: Path) -> int | None:
    """Return total page count, or None if the PDF cannot be parsed."""
    try:
        return len(_PdfReader(str(pdf_path)).pages)
    except Exception as exc:
        print(f"[WARN] Could not read page count ({exc}); "
              "will submit the entire PDF as one segment.")
        return None


def build_page_ranges(total: int, segment_size: int) -> list[str]:
    """Return a list of 'start-end' strings covering [1, total] in steps."""
    ranges: list[str] = []
    start = 1
    while start <= total:
        end = min(start + segment_size - 1, total)
        ranges.append(f"{start}-{end}")
        start = end + 1
    return ranges


# ---------------------------------------------------------------------------
# API steps
# ---------------------------------------------------------------------------

def request_batch(
    session:     requests.Session,
    pdf_name:    str,
    page_ranges: str | None,
    model:       str,
    language:    str,
    is_ocr:      bool,
) -> dict:
    """
    POST /file-urls/batch
    Returns the full parsed JSON body on success; raises SystemExit on error.
    """
    file_entry: dict = {"name": pdf_name, "is_ocr": is_ocr}
    if page_ranges:
        file_entry["page_ranges"] = page_ranges

    body = {
        "enable_formula":  True,
        "enable_table":    True,
        "language":        language,
        "model_version":   model,
        "files":           [file_entry],
    }

    resp = session.post(f"{MINERU_API_BASE}/file-urls/batch", json=body)

    if resp.status_code in (401, 403):
        raise SystemExit(
            f"[ERROR] Authentication failed (HTTP {resp.status_code}). "
            "Check your --token."
        )
    if resp.status_code == 429:
        raise SystemExit(
            "[ERROR] Rate limit / daily quota exceeded (HTTP 429). "
            "Wait and try again, or use a different token."
        )
    if not resp.ok:
        raise SystemExit(
            f"[ERROR] Batch request failed: HTTP {resp.status_code}\n"
            f"{resp.text[:400]}"
        )

    data = resp.json()
    code = data.get("code", 0)
    if code == -60006:
        raise SystemExit(
            "[ERROR] Page count exceeds the server limit (code -60006). "
            "Try a smaller --max-pages value."
        )
    if code != 0:
        raise SystemExit(
            f"[ERROR] API returned error code {code}: {data.get('msg', '(no message)')}"
        )

    return data


def upload_pdf(pdf_bytes: bytes, upload_url: str) -> None:
    """
    PUT raw PDF bytes to the presigned OSS URL.

    IMPORTANT: must NOT send a Content-Type header — the presigned URL
    signature does not cover it, and sending one causes Alibaba Cloud OSS
    to return 403.  Use a plain requests.put() (not the session) so that
    the session's default headers don't leak through.
    """
    resp = requests.put(upload_url, data=pdf_bytes)   # no Content-Type, no auth
    if not (200 <= resp.status_code < 300):
        raise SystemExit(
            f"[ERROR] PDF upload failed: HTTP {resp.status_code}\n"
            f"{resp.text[:300]}"
        )


def poll_until_done(
    session:     requests.Session,
    batch_id:    str,
    timeout_s:   int,
) -> str:
    """
    GET /extract-results/batch/{batch_id} every POLL_INTERVAL_S seconds.
    Returns full_zip_url when state == 'done'.
    """
    url      = f"{MINERU_API_BASE}/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + timeout_s
    elapsed  = 0

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S

        resp = session.get(url)
        if resp.status_code == 429:
            print("    [!] Rate-limited on poll — backing off 10 s…")
            time.sleep(10)
            continue
        if not resp.ok:
            print(f"    [!] Poll HTTP {resp.status_code}, retrying…")
            continue

        results = (resp.json().get("data") or {}).get("extract_result") or []
        if not results:
            continue

        result = results[0]
        state  = result.get("state", "")
        print(f"    [{elapsed:>4}s] state: {state}", flush=True)

        if state == "done":
            zip_url = result.get("full_zip_url", "")
            if not zip_url:
                raise SystemExit(
                    "[ERROR] state=done but full_zip_url is missing in the response."
                )
            return zip_url

        if state == "failed":
            err = result.get("err_msg") or result.get("msg") or "(no error message)"
            raise SystemExit(f"[ERROR] Server extraction failed: {err}")

    raise SystemExit(
        f"[ERROR] Timed out after {timeout_s // 60} minutes waiting for extraction."
    )


def download_and_extract(zip_url: str, out_dir: Path) -> int:
    """
    Download the result ZIP and extract all files except directories and
    __MACOSX metadata entries — mirrors inspectMineruZipBytes() in mineruZip.ts.

    Returns the number of files extracted.
    """
    # Plain requests.get — no auth header needed for the presigned download URL
    resp = requests.get(zip_url, stream=False, timeout=120)
    if not resp.ok:
        raise SystemExit(
            f"[ERROR] ZIP download failed: HTTP {resp.status_code}"
        )

    raw = resp.content

    # Validate ZIP signature (PK magic bytes)
    if len(raw) < 4 or raw[:2] != b"PK":
        raise SystemExit(
            f"[ERROR] Downloaded content is not a ZIP archive "
            f"(first bytes: {raw[:4].hex()}).\n"
            "       The presigned URL may have expired — re-run the script."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        for entry in zf.infolist():
            name = entry.filename
            # Skip directory entries, macOS metadata, and the original PDF
            if name.endswith("/") or name.startswith("__MACOSX/"):
                continue
            if name.lower().endswith(".pdf"):
                continue
            dest = out_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))
            count += 1

    return count


# ---------------------------------------------------------------------------
# Per-segment orchestration
# ---------------------------------------------------------------------------

def process_segment(
    session:     requests.Session,
    pdf_bytes:   bytes,
    pdf_name:    str,
    page_ranges: str | None,
    model:       str,
    language:    str,
    is_ocr:      bool,
    out_dir:     Path,
    label:       str,
    timeout_s:   int,
) -> None:
    """Run all four steps for one page-range segment."""
    pages_display = f" (pages {page_ranges})" if page_ranges else ""
    print(f"\n{'─' * 60}")
    print(f"  Segment {label}{pages_display}")
    print(f"{'─' * 60}")

    # Step 1 — request presigned upload URL + batch_id
    print("  [1/4] Requesting upload URL…")
    batch_resp = request_batch(session, pdf_name, page_ranges, model, language, is_ocr)
    batch_data = batch_resp.get("data") or {}
    batch_id   = batch_data.get("batch_id")
    file_urls  = batch_data.get("file_urls") or []

    if not batch_id or not file_urls:
        raise SystemExit(
            "[ERROR] Missing batch_id or file_urls in response:\n"
            + json.dumps(batch_resp, ensure_ascii=False, indent=2)[:600]
        )
    print(f"        batch_id: {batch_id}")

    # Step 2 — PUT upload
    size_mb = len(pdf_bytes) / 1024 / 1024
    print(f"  [2/4] Uploading PDF ({size_mb:.1f} MB)…")
    upload_pdf(pdf_bytes, file_urls[0])
    print("        Upload complete.")

    # Step 3 — poll
    print("  [3/4] Waiting for server processing…")
    zip_url = poll_until_done(session, batch_id, timeout_s)

    # Step 4 — download + extract
    print("  [4/4] Downloading and extracting ZIP…")
    n = download_and_extract(zip_url, out_dir)
    print(f"        Extracted {n} files → {out_dir}")


# ---------------------------------------------------------------------------
# Title extraction & filename sanitisation
# ---------------------------------------------------------------------------

def _extract_title_from_layout(layout_path: Path) -> str | None:
    """
    Read layout.json and return the text of the first type='title' block on page 0.
    Returns None if the file is missing, unreadable, or contains no title block.
    """
    try:
        layout = json.loads(layout_path.read_bytes().decode("utf-8"))
        page0  = (layout.get("pdf_info") or [{}])[0]
        for block in page0.get("para_blocks", []):
            if block.get("type") == "title":
                text = " ".join(
                    span.get("content", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                if text:
                    return text
    except Exception:
        pass
    return None


def _clean_for_path(title: str) -> str:
    """
    Sanitise a string for use as a folder / file name:
      - strip Windows/POSIX reserved chars:  \\ / : * ? " < > |
      - replace whitespace runs with a single _
      - collapse consecutive _ into one
      - strip leading/trailing dots and _
      - truncate to 120 characters
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title)
    cleaned = re.sub(r"\s+",                   "_", cleaned)
    cleaned = re.sub(r"_+",                    "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned[:120] or "untitled"


# ---------------------------------------------------------------------------
# Merge  (auto-called after multi-segment parse)
# ---------------------------------------------------------------------------

def merge_parts(out_base: Path) -> None:
    """
    Merge all part_xxx/ sub-folders under out_base into a single set of files
    directly in out_base:
        images/                  — all segment images copied here (hash names, no collision)
        full_merged.md           — markdown files concatenated in order
        content_list_merged.json — content_list arrays merged, page_idx offset applied
        layout_merged.json       — layout pdf_info lists merged, page_idx offset applied

    The part_xxx/ folders are left untouched as raw backups.
    """
    parts = sorted(out_base.glob("part_*/"))
    if not parts:
        return

    print(f"\n{'─' * 60}")
    print(f"  Merging {len(parts)} segments → {out_base}")
    print(f"{'─' * 60}")

    merged_images_dir = out_base / "images"
    merged_images_dir.mkdir(exist_ok=True)

    md_sections:      list[str]  = []
    content_list_all: list[dict] = []
    pdf_info_all:     list[dict] = []
    layout_meta: dict = {}   # _backend, _version_name from first segment

    page_offset = 0

    for part_dir in parts:
        label = part_dir.name

        # ── images ────────────────────────────────────────────────────────
        img_src = part_dir / "images"
        if img_src.is_dir():
            copied = 0
            for img in img_src.iterdir():
                if img.is_file():
                    shutil.copy2(img, merged_images_dir / img.name)
                    copied += 1
            print(f"  [{label}] images: {copied} file(s) copied")

        # ── full.md ───────────────────────────────────────────────────────
        md_file = part_dir / "full.md"
        if md_file.exists():
            md_sections.append(md_file.read_text(encoding="utf-8").rstrip())
            print(f"  [{label}] full.md: appended")

        # ── content_list.json  (page_idx += offset) ───────────────────────
        cl_files = sorted(part_dir.glob("*_content_list.json"))
        max_page_in_segment = 0
        if cl_files:
            items: list[dict] = json.loads(cl_files[0].read_bytes().decode("utf-8"))
            if items:
                max_page_in_segment = max(it["page_idx"] for it in items)
            for item in items:
                item["page_idx"] += page_offset
            content_list_all.extend(items)
            print(f"  [{label}] content_list: {len(items)} items, "
                  f"page_idx offset +{page_offset}")

        # ── layout.json  (pdf_info page_idx += offset) ────────────────────
        layout_file = part_dir / "layout.json"
        if layout_file.exists():
            layout: dict = json.loads(layout_file.read_bytes().decode("utf-8"))
            if not layout_meta:
                layout_meta = {
                    "_backend":      layout.get("_backend", ""),
                    "_version_name": layout.get("_version_name", ""),
                }
            for page in layout.get("pdf_info", []):
                page["page_idx"] += page_offset
                pdf_info_all.append(page)
            print(f"  [{label}] layout.json: {len(layout.get('pdf_info', []))} pages merged")

        # advance offset by the number of pages this segment contained
        page_offset += max_page_in_segment + 1

    # ── write merged files ────────────────────────────────────────────────
    md_out = out_base / "full_merged.md"
    md_out.write_text("\n\n".join(md_sections), encoding="utf-8")
    print(f"\n  ✓ full_merged.md  ({len(md_sections)} sections)")

    cl_out = out_base / "content_list_merged.json"
    cl_out.write_bytes(
        json.dumps(content_list_all, ensure_ascii=False, indent=2).encode("utf-8")
    )
    print(f"  ✓ content_list_merged.json  ({len(content_list_all)} items)")

    if pdf_info_all:
        layout_out = out_base / "layout_merged.json"
        layout_merged = {"pdf_info": pdf_info_all, **layout_meta}
        layout_out.write_bytes(
            json.dumps(layout_merged, ensure_ascii=False, indent=2).encode("utf-8")
        )
        print(f"  ✓ layout_merged.json  ({len(pdf_info_all)} pages)")

    print(f"\n  Merge complete → {out_base}")


# ---------------------------------------------------------------------------
# Public API  (called directly from run.py or any other script)
# ---------------------------------------------------------------------------

def parse_pdf(
    pdf:         str | Path,
    token:       str,
    out:         str | Path | None = None,
    model:       str = "pipeline",
    lang:        str = "ch",
    ocr:         bool = False,
    max_pages:   int = 200,
    timeout:     int = 30,
    page_ranges: str | None = None,
) -> Path:
    """
    Parse a local PDF with MinerU cloud API and extract results to disk.

    Parameters
    ----------
    pdf         : Path to the PDF file.
    token       : MinerU API token (mineru.net).
    out         : Output base directory.  Defaults to <project>/output/<pdf_stem>/.
    model       : "pipeline" (default) or "vlm".
    lang        : Language hint.  Common values:
                    "ch"          — Chinese + English (default)
                    "ch_server"   — Chinese / English / Traditional Chinese / Japanese
                    "en"          — English only
                    "latin"       — French, German, Spanish, Italian, Portuguese, …
                    "japan"       — Japanese
                    "korean"      — Korean
                    "arabic"      — Arabic
                    "cyrillic"    — Russian and other Cyrillic-script languages
                    "devanagari"  — Hindi, Sanskrit, …
    ocr         : Force OCR mode (is_ocr=True).  Use for scanned PDFs.
    max_pages   : Max pages per auto-split segment (default 200).  Ignored when
                  page_ranges is set.
    timeout     : Per-segment poll timeout in minutes (default 30).
    page_ranges : Manual page selection passed directly to the API, e.g. "1-50"
                  or "1-50,80-100".  When set, auto-split is skipped and the
                  entire string is submitted as a single task.
                  Format: comma-separated pages/ranges, e.g. "2,4-6,10-20".
                  Use "2--2" to mean "page 2 to second-to-last page".

    Returns
    -------
    Path  — the output base directory where results were written.
    """
    pdf_path  = Path(pdf).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        print(f"[WARN] File does not end with .pdf: {pdf_path.name}")

    out_base  = Path(out).resolve() if out else DEFAULT_OUTPUT_DIR / pdf_path.stem
    timeout_s = timeout * 60
    pdf_name  = _sanitize_filename(pdf_path.name)

    print(f"PDF    : {pdf_path}")
    print(f"Output : {out_base}")
    print(f"Model  : {model}   Language: {lang}   OCR: {ocr}")

    pdf_bytes = pdf_path.read_bytes()

    # ── Determine segments ────────────────────────────────────────────────────
    if page_ranges:
        # Manual override: submit exactly the user-specified range as one task
        page_ranges_list: list[str | None] = [page_ranges]
        print(f"Pages  : manual range — {page_ranges}")
    else:
        total_pages = get_pdf_page_count(pdf_path)
        if total_pages is not None:
            print(f"Pages  : {total_pages}")
            page_ranges_list = build_page_ranges(total_pages, max_pages)  # type: ignore[assignment]
        else:
            page_ranges_list = [None]

    n_seg = len(page_ranges_list)
    if n_seg == 1:
        print("Segments: 1 (full PDF)")
    else:
        preview = ", ".join(str(r) for r in page_ranges_list[:5])
        suffix  = ", …" if n_seg > 5 else ""
        print(f"Segments: {n_seg}  ({preview}{suffix})")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })

    for i, page_ranges in enumerate(page_ranges_list, start=1):
        out_dir = out_base / f"part_{i:03d}" if n_seg > 1 else out_base
        process_segment(
            session     = session,
            pdf_bytes   = pdf_bytes,
            pdf_name    = pdf_name,
            page_ranges = page_ranges,
            model       = model,
            language    = lang,
            is_ocr      = ocr,
            out_dir     = out_dir,
            label       = f"{i}/{n_seg}",
            timeout_s   = timeout_s,
        )

    print(f"\n{'=' * 60}")
    print(f"  All {n_seg} segment(s) complete.")
    print(f"{'=' * 60}")

    if n_seg > 1:
        merge_parts(out_base)

    # ── Extract title from layout.json and rename output dir + md file ────
    layout_for_title = (
        out_base / "part_001" / "layout.json" if n_seg > 1
        else out_base / "layout.json"
    )
    title_raw   = _extract_title_from_layout(layout_for_title)
    title_clean = _clean_for_path(title_raw) if title_raw else None

    if title_clean:
        print(f"\n  Title detected: {title_raw}")

        # Rename output directory (only when using the default output path)
        if not out:
            new_base = out_base.parent / title_clean
            if new_base != out_base:
                if new_base.exists():
                    print(f"  [WARN] Target dir already exists, keeping original name: {out_base.name}")
                else:
                    out_base.rename(new_base)
                    out_base = new_base
                    print(f"  Output dir → {out_base.name}")

        # Rename the final md file
        md_src_name = "full_merged.md" if n_seg > 1 else "full.md"
        md_src = out_base / md_src_name
        if md_src.exists():
            md_dst = out_base / f"{title_clean}.md"
            md_src.rename(md_dst)
            print(f"  MD file    → {md_dst.name}")
    else:
        print("\n  [WARN] No title block found in layout.json; keeping default file names.")

    print(f"\n  Results: {out_base}")
    return out_base


# ---------------------------------------------------------------------------
# CLI  (kept so the script still works from the terminal if needed)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mineru_client.py",
        description="MinerU cloud PDF parser — splits large PDFs and saves results locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mineru_client.py paper.pdf --token sk-xxxx
  python mineru_client.py thesis.pdf --token sk-xxxx --out ./results
  python mineru_client.py big.pdf    --token sk-xxxx --max-pages 100
  python mineru_client.py scan.pdf   --token sk-xxxx --ocr --lang en
  python mineru_client.py doc.pdf    --token sk-xxxx --model vlm
""",
    )
    parser.add_argument("pdf",        help="Path to the local PDF file to parse.")
    parser.add_argument("--token",    required=True, help="MinerU API token.")
    parser.add_argument("--out",      default=None,  help="Output directory.")
    parser.add_argument("--model",    choices=["pipeline", "vlm"], default="pipeline")
    parser.add_argument("--lang",     default="ch")
    parser.add_argument("--ocr",      action="store_true")
    parser.add_argument("--max-pages",   type=int, default=200)
    parser.add_argument("--timeout",     type=int, default=30)
    parser.add_argument("--page-ranges", type=str, default=None,
                        help="Manual page selection, e.g. '1-50' or '1-50,80-100'. "
                             "Overrides auto-split when set.")

    args = parser.parse_args()
    try:
        parse_pdf(
            pdf         = args.pdf,
            token       = args.token,
            out         = args.out,
            model       = args.model,
            lang        = args.lang,
            ocr         = args.ocr,
            max_pages   = args.max_pages,
            timeout     = args.timeout,
            page_ranges = args.page_ranges,
        )
    except FileNotFoundError as e:
        sys.exit(f"[ERROR] {e}")


if __name__ == "__main__":
    main()
