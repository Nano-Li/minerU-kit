# minerU-kit

A simple utility script that bypasses MinerU's **200-page single-upload limit** — it automatically splits large PDFs into segments, uploads and parses each segment via the **MinerU cloud API v4**, then auto-merges all results into a single clean output.

---

## Features

- Reads a local PDF and submits it to MinerU's precise-parsing API
- **Auto-splits** PDFs longer than 200 pages into segments (configurable), submits each as a separate batch task
- After all segments finish, **auto-merges** results into a single output folder:
  - `full_merged.md` — concatenated Markdown
  - `content_list_merged.json` — merged content list with `page_idx` offsets applied
  - `layout_merged.json` — merged layout data with `page_idx` offsets applied
  - `images/` — all extracted images (hash-named, no filename collision across segments)
- Raw per-segment folders (`part_001/`, `part_002/`, …) are kept as backups

---

## Requirements

```bash
pip install requests pypdf
```

| Package    | Purpose                                      |
|------------|----------------------------------------------|
| `requests` | HTTP calls to MinerU API and OSS storage     |
| `pypdf`    | Read PDF page count for auto-segmentation    |

---

## Quick Start

### Option A — Edit `run.py` and click Run (recommended)

`run.py` is **not committed to the repo** (it contains your personal token and local paths).  
Create it yourself by copying the template below:

```python
"""
run.py — fill in the config below, then click Run in your IDE.
"""
from mineru_client import parse_pdf

# ════════════════════════════════════════════════════════════════════════════
# ── Configuration (edit this block only) ────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# Required: path to local PDF file (use r"..." for Windows paths)
PDF = r"C:\path\to\your\paper.pdf"

# Required: MinerU API token — obtain from mineru.net
TOKEN = "your_mineru_api_token_here"

# Optional: output directory. Leave empty → output/<title>/ inside the project
OUT = ""

# Parser model: "pipeline" (default, fast) or "vlm" (better for complex layouts)
MODEL = "pipeline"

# Document language:
#   "ch"         — Chinese + English (default)
#   "ch_server"  — Chinese / English / Traditional Chinese / Japanese
#   "en"         — English only
#   "latin"      — French, German, Spanish, Italian, Portuguese, etc.
#   "japan"      — Japanese
#   "korean"     — Korean
#   "arabic"     — Arabic
#   "cyrillic"   — Russian, etc.
#   "devanagari" — Hindi, Sanskrit, etc.
LANG = "en"

# Force OCR mode — recommended for scanned (image-only) PDFs
OCR = False

# Manual page range (leave empty to process the entire PDF)
# Examples:
#   "1-50"        — parse pages 1–50 only
#   "1-50,80-100" — parse pages 1–50 and 80–100
#   "2--2"        — from page 2 to the second-to-last page
# Note: when set, MAX_PAGES auto-segmentation is ignored
PAGE_RANGES = ""

# Max pages per segment; PDFs exceeding this are split automatically (default 200)
# Ignored when PAGE_RANGES is set
MAX_PAGES = 200

# Per-segment poll timeout in minutes (default 30)
TIMEOUT = 30

# ════════════════════════════════════════════════════════════════════════════
# ── No changes needed below ─────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parse_pdf(
        pdf         = PDF,
        token       = TOKEN,
        out         = OUT or None,
        model       = MODEL,
        lang        = LANG,
        ocr         = OCR,
        max_pages   = MAX_PAGES,
        timeout     = TIMEOUT,
        page_ranges = PAGE_RANGES or None,
    )
```

### Option B — Command line

```bash
python mineru_client.py paper.pdf --token sk-xxxx
python mineru_client.py thesis.pdf --token sk-xxxx --out ./results --lang en
python mineru_client.py big.pdf    --token sk-xxxx --max-pages 100
python mineru_client.py scan.pdf   --token sk-xxxx --ocr
python mineru_client.py doc.pdf    --token sk-xxxx --model vlm
```

---

## Parameters

| Parameter       | Default      | Description                                                                                               |
|-----------------|--------------|-----------------------------------------------------------------------------------------------------------|
| `pdf`           | *(required)* | Path to the local PDF file                                                                                |
| `--token`       | *(required)* | MinerU API token — obtain from [mineru.net](https://mineru.net)                                           |
| `--out`         | auto         | Output directory (default: `output/<title>/` inside the project)                                         |
| `--model`       | `pipeline`   | Parser model: `pipeline` (fast) or `vlm` (better for complex layouts)                                    |
| `--lang`        | `ch`         | Language hint: `ch`, `ch_server`, `en`, `latin` (French/German/Spanish/…), `japan`, `korean`, `arabic`, `cyrillic`, `devanagari` |
| `--ocr`         | `False`      | Force OCR mode — use for scanned PDFs                                                                     |
| `--page-ranges` | —            | Parse specific pages only, e.g. `"1-50"`, `"1-50,80-100"`, `"2--2"`. Disables auto-segmentation.        |
| `--max-pages`   | `200`        | Max pages per segment; larger PDFs are split automatically (ignored when `--page-ranges` is set)          |
| `--timeout`     | `30`         | Per-segment poll timeout in minutes                                                                       |

---

## Output Structure

**Single segment (≤ 200 pages):**

```
paper_mineru/
    full.md
    images/
    <uuid>_content_list.json
    layout.json
```

**Multi-segment (> 200 pages, auto-merged):**

```
paper_mineru/
    images/                    ← all images from every segment
    full_merged.md             ← concatenated Markdown
    content_list_merged.json   ← page_idx offsets applied
    layout_merged.json         ← page_idx offsets applied
    part_001/                  ← raw segment (backup)
    part_002/
    ...
```

---

## API Flow (mirrors mineruClient.ts)

1. `POST /api/v4/file-urls/batch` — request presigned upload URL + `batch_id`
2. `PUT <presigned_url>` — upload raw PDF bytes *(no Content-Type header — required by Alibaba Cloud OSS)*
3. `GET /api/v4/extract-results/batch/{batch_id}` — poll every 3 s until `state == "done"`
4. Download `full_zip_url`, extract with Python `zipfile`

---

## Security Note

**Never commit your API token.**  
`run.py` is listed in `.gitignore` because it contains your personal token and local file paths.  
Use `run.py` locally only; share code via `mineru_client.py`.

---

## Reference

- MinerU API docs: <https://mineru.net/doc/docs/index_en>
- MinerU output format: <https://opendatalab.github.io/MinerU/reference/output_files/>
