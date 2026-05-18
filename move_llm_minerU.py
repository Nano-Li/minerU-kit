"""
move_llm_minerU.py — 批量整理 MinerU 解析输出，按论文标题重命名并复制到目标目录。

使用方法：
  1. 修改下方配置区中的 SOURCE_DIR 和 TARGET_DIR
  2. 在编辑器中直接运行本文件（Run / ▶）

功能：
  - 遍历 SOURCE_DIR 下每个子文件夹（每个子文件夹 = 一篇论文的解析输出）
  - 从 layout.json 中程序化提取论文标题
  - md 文件直接复制到 TARGET_DIR/<论文标题>.md（不创建子文件夹）
  - 图片统一复制到 TARGET_DIR/images/（自动创建，多篇论文共享同一 images 目录）
  - 若目标已存在同名 .md 则跳过，不覆盖
  - 源目录原封不动
"""

import shutil
import sys
from pathlib import Path

from mineru_client import _clean_for_path, _extract_title_from_layout

# ════════════════════════════════════════════════════════════════════════════
# ── 配置区（只改这里）────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

# 提取文件夹：包含多个论文解析输出子文件夹的目录
SOURCE_DIR = r"D:\Research\article_mds"

# 目标文件夹：整理后的论文库
TARGET_DIR = r"D:\obsidian\AeroDynamic\raw\论文"

# ════════════════════════════════════════════════════════════════════════════
# ── 以下无需修改 ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

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
    处理单个论文解析输出目录：
      - md 文件 → target_base/<cleaned_title>.md
      - images/ → target_base/images/（与其他论文共享，逐文件复制，已存在则跳过）

    返回 True 表示成功处理，False 表示跳过。
    """
    # ── 1. 提取标题 ──────────────────────────────────────────────────────────
    layout_path = src_dir / "layout.json"
    title_raw   = _extract_title_from_layout(layout_path) if layout_path.exists() else None

    if title_raw:
        cleaned_title = _clean_for_path(title_raw)
        print(f"\n  论文标题: {title_raw}")
    else:
        cleaned_title = _clean_for_path(src_dir.name)
        print(f"\n  [WARN] 未找到标题块，使用文件夹名作为 fallback: {src_dir.name}")

    # ── 2. 检查目标 md 是否已存在（防止覆盖）────────────────────────────────
    dst_md = target_base / f"{cleaned_title}.md"
    if dst_md.exists():
        print(f"  [SKIP] 目标已存在，跳过: {dst_md.name}")
        return False

    # ── 3. 找主 md 文件 ───────────────────────────────────────────────────────
    md_src = _pick_md_file(src_dir)
    if md_src is None:
        print(f"  [SKIP] 未找到 .md 文件，跳过: {src_dir.name}")
        return False

    # ── 4. 复制 md → target_base/<cleaned_title>.md ───────────────────────────
    shutil.copy2(md_src, dst_md)
    print(f"  [md]  {md_src.name} → {dst_md.name}")

    # ── 5. 复制 images/* → target_base/images/（逐文件，已存在则跳过）────────
    src_images = src_dir / "images"
    img_copied = 0
    img_skipped = 0
    if src_images.is_dir():
        dst_images = target_base / "images"
        dst_images.mkdir(exist_ok=True)
        for img in src_images.iterdir():
            if img.is_file():
                dst_img = dst_images / img.name
                if dst_img.exists():
                    img_skipped += 1
                else:
                    shutil.copy2(img, dst_img)
                    img_copied += 1
        print(f"  [img] 复制 {img_copied} 张，已存在跳过 {img_skipped} 张")

    print(f"  [✓] {src_dir.name} → {dst_md.name}")
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
    print(f"目标结构：{target}/<title>.md  +  {target}/images/")
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
