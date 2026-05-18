"""
move_llm_minerU.py — 批量整理 MinerU 解析输出，按论文标题重命名并复制到目标目录。

使用方法：
  1. 修改下方配置区中的 SOURCE_DIR 和 TARGET_DIR
  2. 在编辑器中直接运行本文件（Run / ▶）

功能：
  - 遍历 SOURCE_DIR 下每个子文件夹（每个子文件夹 = 一篇论文的解析输出）
  - 从 layout.json 中程序化提取论文标题
  - 以清洗后的标题重命名，**复制**到 TARGET_DIR（源目录原封不动）
  - 复制时排除：layout.json、layout_merged.json、*_origin.pdf
  - 主 md 文件重命名为 <论文标题>.md
"""

import shutil
import sys
from pathlib import Path

from mineru_client import _clean_for_path, _extract_title_from_layout

# ════════════════════════════════════════════════════════════════════════════
# ── 配置区（只改这里）────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# 提取文件夹：包含多个论文解析输出子文件夹的目录
SOURCE_DIR = r"D:\Projects\pdf_transform\output"

# 目标文件夹：整理后的论文库
TARGET_DIR = r"D:\path\to\clean_library"

# ════════════════════════════════════════════════════════════════════════════
# ── 以下无需修改 ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# 文件名完全匹配时排除
_EXCLUDE_NAMES: set[str] = {"layout.json", "layout_merged.json"}

# 文件名后缀匹配时排除（小写）
_EXCLUDE_SUFFIXES: tuple[str, ...] = ("_origin.pdf",)


def _should_exclude(filename: str) -> bool:
    """返回 True 表示该文件需要跳过，不复制到目标目录。"""
    if filename in _EXCLUDE_NAMES:
        return True
    lower = filename.lower()
    return any(lower.endswith(s) for s in _EXCLUDE_SUFFIXES)


def _pick_md_file(src_dir: Path) -> Path | None:
    """
    从 src_dir 根目录找主 md 文件。
    优先取名称最短的那个（full.md < full_merged.md < 其他）。
    若有多个则打印警告。
    """
    md_files = [f for f in src_dir.iterdir() if f.is_file() and f.suffix.lower() == ".md"]
    if not md_files:
        return None
    if len(md_files) > 1:
        print(f"  [WARN] 发现多个 .md 文件，取名称最短的: {[f.name for f in md_files]}")
    return min(md_files, key=lambda f: len(f.name))


def process_one_folder(src_dir: Path, target_base: Path) -> bool:
    """
    处理单个论文解析输出目录，复制到 target_base/<cleaned_title>/。

    返回 True 表示成功复制，False 表示跳过。
    """
    # ── 1. 提取标题 ──────────────────────────────────────────────────────────
    layout_path = src_dir / "layout.json"
    title_raw   = _extract_title_from_layout(layout_path) if layout_path.exists() else None

    if title_raw:
        cleaned_title = _clean_for_path(title_raw)
        print(f"\n  论文标题: {title_raw}")
    else:
        # fallback：使用原文件夹名（同样清洗）
        cleaned_title = _clean_for_path(src_dir.name)
        print(f"\n  [WARN] 未找到标题块，使用文件夹名作为 fallback: {src_dir.name}")

    # ── 2. 检查目标是否已存在 ─────────────────────────────────────────────────
    dst_dir = target_base / cleaned_title
    if dst_dir.exists():
        print(f"  [SKIP] 目标已存在，跳过: {dst_dir}")
        return False

    # ── 3. 找主 md 文件 ───────────────────────────────────────────────────────
    md_src = _pick_md_file(src_dir)

    # ── 4. 创建目标文件夹并逐项复制 ──────────────────────────────────────────
    dst_dir.mkdir(parents=True, exist_ok=True)

    copied_files  = 0
    skipped_files = 0

    for item in src_dir.iterdir():
        # 目录（如 images/）→ 整体 copytree
        if item.is_dir():
            shutil.copytree(item, dst_dir / item.name)
            copied_files += 1
            continue

        # 文件：检查排除规则
        if _should_exclude(item.name):
            skipped_files += 1
            continue

        # 主 md 文件 → 重命名复制
        if md_src and item == md_src:
            dst_name = f"{cleaned_title}.md"
            shutil.copy2(item, dst_dir / dst_name)
            copied_files += 1
            continue

        # 其余文件 → 原名复制
        shutil.copy2(item, dst_dir / item.name)
        copied_files += 1

    print(f"  [✓] {src_dir.name}")
    print(f"      → {dst_dir}")
    print(f"      复制 {copied_files} 项，跳过 {skipped_files} 项（排除文件）")
    return True


def main() -> None:
    source = Path(SOURCE_DIR)
    target = Path(TARGET_DIR)

    # ── 入参校验 ──────────────────────────────────────────────────────────────
    if not source.exists():
        sys.exit(f"[ERROR] SOURCE_DIR 不存在: {source}")
    if not source.is_dir():
        sys.exit(f"[ERROR] SOURCE_DIR 不是目录: {source}")

    target.mkdir(parents=True, exist_ok=True)

    # ── 枚举子目录 ────────────────────────────────────────────────────────────
    subdirs = sorted(d for d in source.iterdir() if d.is_dir())
    if not subdirs:
        print(f"[INFO] SOURCE_DIR 下没有子文件夹: {source}")
        return

    print(f"SOURCE : {source}")
    print(f"TARGET : {target}")
    print(f"找到 {len(subdirs)} 个子文件夹，开始处理…")
    print("─" * 60)

    ok_count   = 0
    skip_count = 0

    for sub in subdirs:
        success = process_one_folder(sub, target)
        if success:
            ok_count += 1
        else:
            skip_count += 1

    print("\n" + "═" * 60)
    print(f"  完成：成功复制 {ok_count} 个，跳过 {skip_count} 个")
    print(f"  结果目录: {target}")
    print("═" * 60)


if __name__ == "__main__":
    main()
