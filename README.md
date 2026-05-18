# pdf_transform

A lightweight Python tool that calls the **MinerU cloud API v4** to parse local PDF files into Markdown + structured JSON.  
Mirrors the upload/poll/extract flow from [llm-for-zotero](https://github.com/yilewang/llm-for-zotero).

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
# ── 参数配置（每次改这里就好）──────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# 必填：本地 PDF 文件路径（Windows 路径用 r"..." 或正斜杠）
PDF = r"C:\path\to\your\paper.pdf"

# 必填：MinerU API Token（在 mineru.net 申请）
TOKEN = "your_mineru_api_token_here"

# 选填：输出目录。留空则自动输出到项目 output/ 文件夹，并以文章标题命名子文件夹
OUT = ""

# 解析模型："pipeline"（默认，速度快）或 "vlm"（复杂版式更准）
MODEL = "pipeline"

# 文档语言："ch"（中文，默认）、"en"（英文）、"ja"（日文）等
LANG = "en"

# 是否强制 OCR（扫描版 PDF 建议设为 True）
OCR = False

# 每段最大页数（超过此页数的 PDF 会自动分段提交，默认 200）
MAX_PAGES = 200

# 每段等待超时（分钟，默认 30）
TIMEOUT = 30

# ════════════════════════════════════════════════════════════════════════════
# ── 以下无需修改 ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parse_pdf(
        pdf       = PDF,
        token     = TOKEN,
        out       = OUT or None,
        model     = MODEL,
        lang      = LANG,
        ocr       = OCR,
        max_pages = MAX_PAGES,
        timeout   = TIMEOUT,
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

| Parameter     | Default      | Description                                                      |
|---------------|--------------|------------------------------------------------------------------|
| `pdf`         | *(required)* | Path to the local PDF file                                       |
| `--token`     | *(required)* | MinerU API token — obtain from [mineru.net](https://mineru.net)  |
| `--out`       | auto         | Output directory (default: `output/<title>/` inside the project)  |
| `--model`     | `pipeline`   | Parser model: `pipeline` (fast) or `vlm` (better layout)        |
| `--lang`      | `ch`         | Language hint: `ch`, `en`, `ja`, `fr`, …                        |
| `--ocr`       | `False`      | Force OCR mode — use for scanned PDFs                            |
| `--max-pages` | `200`        | Max pages per segment; larger PDFs are split automatically       |
| `--timeout`   | `30`         | Per-segment poll timeout in minutes                              |

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
