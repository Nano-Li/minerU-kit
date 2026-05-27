"""
batch_run.py — 批量解析工作文件夹中的所有 PDF，输出扁平化到 output/ 子文件夹。

使用方法：
  1. 修改下方 ── 配置区 ── 中的参数
  2. 在编辑器中直接运行（Run / ▶）

输出结构：
  WORK_DIR/
      output/
          论文标题_A.md
          论文标题_B.md
          images/
              abc123.png
              def456.png
              ...
      .processed          ← 已处理 PDF 的记录文件（自动维护，防止重复提交）

注意：
  - 每次运行时，已成功处理的 PDF 会被跳过（依据 .processed 记录）
  - 删除 output/.processed 可强制重新处理所有文件
  - 若 output/ 中已存在同名 .md，自动追加编号 (1)(2)… 而非跳过，保证每个 PDF 都有输出
"""

import shutil
import sys
from pathlib import Path

from mineru_client import parse_pdf

# ════════════════════════════════════════════════════════════════════════════
# ── 配置区（每次改这里就好）────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# 必填：包含多个 PDF 的工作文件夹
WORK_DIR = r"D:\path\to\your\pdf_folder"

# 必填：MinerU API Token（在 mineru.net 申请）
TOKEN = "your_mineru_api_token_here"

# 解析模型："pipeline"（默认，速度快）或 "vlm"（复杂版式更准）
MODEL = "pipeline"

# 文档语言，可选值：
#   "ch"         — 中文 + 英文（默认）
#   "ch_server"  — 中文 / 英文 / 繁体中文 / 日文
#   "en"         — 纯英文
#   "latin"      — 法语、德语、西班牙语、意大利语、葡萄牙语等拉丁字母语言
#   "japan"      — 日文
#   "korean"     — 韩文
#   "arabic"     — 阿拉伯语
#   "cyrillic"   — 俄语等西里尔字母语言
#   "devanagari" — 印地语、梵文等
LANG = "en"

# 是否强制 OCR（扫描版 PDF 建议设为 True）
OCR = False

# 是否用 PDF 文件名命名输出（True = 用原始文件名；False = 用提取出的论文标题，默认）
USE_FILENAME = False

# 每段最大页数（超过此页数的 PDF 会自动分段提交，默认 200）
MAX_PAGES = 200

# 每段等待超时（分钟，默认 30）
TIMEOUT = 30

# ════════════════════════════════════════════════════════════════════════════
# ── 以下无需修改 ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════


def _unique_md_path(flat_output: Path, preferred_stem: str) -> Path:
    """
    返回 flat_output/<preferred_stem>.md；
    若该路径已存在，则依次尝试 <stem>(1).md、<stem>(2).md … 直到找到空位。
    """
    candidate = flat_output / f"{preferred_stem}.md"
    counter = 1
    while candidate.exists():
        candidate = flat_output / f"{preferred_stem}({counter}).md"
        counter += 1
    return candidate


def _flatten_to_output(per_paper_dir: Path, flat_output: Path, pdf_stem: str) -> None:
    """
    将 parse_pdf 生成的论文子文件夹内容压平到 flat_output：
      - <per_paper_dir>/<title>.md  →  flat_output/<title>.md
      - <per_paper_dir>/images/*    →  flat_output/images/（逐文件，已存在则跳过）
    完成后删除 per_paper_dir。

    命名规则（优先级递减）：
      1. 论文标题（parse_pdf 提取并重命名后的 md 文件名）
      2. 若标题提取失败（md 仍为 full.md / full_merged.md），改用 PDF 文件名（pdf_stem）
      3. 若目标路径已存在（标题重复），自动追加 (1)(2)… 确保不丢失任何结果
    """
    flat_output.mkdir(parents=True, exist_ok=True)
    images_dst = flat_output / "images"
    images_dst.mkdir(exist_ok=True)

    # ── 移动 .md 文件 ────────────────────────────────────────────────────────
    md_files = list(per_paper_dir.glob("*.md"))
    for md in md_files:
        # 标题提取失败时 md 名为 full.md / full_merged.md，改用 PDF 原文件名
        if md.stem in ("full", "full_merged"):
            preferred_stem = pdf_stem
        else:
            preferred_stem = md.stem

        dst_md = _unique_md_path(flat_output, preferred_stem)
        if dst_md.stem != preferred_stem:
            print(f"  [WARN] 标题重复，自动重命名: {md.name} → {dst_md.name}")
        shutil.move(str(md), dst_md)
        print(f"  [md]  → {dst_md.name}")

    # ── 移动 images ──────────────────────────────────────────────────────────
    img_src = per_paper_dir / "images"
    if img_src.is_dir():
        moved = skipped = 0
        for img in img_src.iterdir():
            if img.is_file():
                dst_img = images_dst / img.name
                if dst_img.exists():
                    skipped += 1
                else:
                    shutil.move(str(img), dst_img)
                    moved += 1
        print(f"  [img] 移动 {moved} 张，已存在跳过 {skipped} 张")

    # ── 删除已清空的论文子文件夹 ─────────────────────────────────────────────
    shutil.rmtree(per_paper_dir, ignore_errors=True)


def main() -> None:
    work_dir = Path(WORK_DIR)
    if not work_dir.is_dir():
        sys.exit(f"[ERROR] WORK_DIR 不存在: {work_dir}")

    pdfs = sorted(work_dir.glob("*.pdf"))
    if not pdfs:
        print(f"[INFO] 未在工作文件夹中找到 PDF 文件: {work_dir}")
        return

    flat_output = work_dir / "output"
    flat_output.mkdir(exist_ok=True)

    # ── 读取已处理记录 ────────────────────────────────────────────────────────
    done_log  = flat_output / ".processed"
    done_set: set[str] = set()
    if done_log.exists():
        done_set = set(done_log.read_text(encoding="utf-8").splitlines())

    print(f"工作目录 : {work_dir}")
    print(f"输出目录 : {flat_output}")
    print(f"找到     : {len(pdfs)} 个 PDF")
    print("═" * 60)

    ok = skipped = failed = 0

    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}] {pdf.name}")

        # 已成功处理过则跳过
        if pdf.name in done_set:
            print(f"  [SKIP] 已处理，跳过")
            skipped += 1
            continue

        try:
            per_paper_dir = parse_pdf(
                pdf          = pdf,
                token        = TOKEN,
                out          = flat_output,
                model        = MODEL,
                lang         = LANG,
                ocr          = OCR,
                max_pages    = MAX_PAGES,
                timeout      = TIMEOUT,
                use_filename = USE_FILENAME,
            )
            _flatten_to_output(per_paper_dir, flat_output, pdf.stem)

            # 记录为已处理
            with done_log.open("a", encoding="utf-8") as f:
                f.write(pdf.name + "\n")
            done_set.add(pdf.name)
            ok += 1

        except SystemExit as e:
            print(f"\n  [ERROR] {e}")
            failed += 1
        except Exception as e:
            print(f"\n  [ERROR] 未预期的错误: {e}")
            failed += 1

    print("\n" + "═" * 60)
    print(f"  完成：成功 {ok} 个，跳过 {skipped} 个，失败 {failed} 个")
    print(f"  输出目录: {flat_output}")
    print("═" * 60)


if __name__ == "__main__":
    main()
