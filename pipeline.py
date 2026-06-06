#!/usr/bin/env python3
"""
Books_Converter — PDF → EPUB 全自动转换管线

用法:
    python pipeline.py <pdf_path> [--output-dir <dir>] [--ocr/--no-ocr]

示例:
    python pipeline.py "D:\books\mybook.pdf"
    python pipeline.py "D:\books\mybook.pdf" --output-dir "F:\output"
    python pipeline.py "D:\books\mybook.pdf" --no-ocr   # 文字版 PDF
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")

from pathlib import Path

from stage1_mineru import run_mineru, save_mineru_metadata
from stage2_deepseek import analyze_structure, save_structure
from stage3_epub import generate_epub
from progress_ui import ProgressWindow


def main():
    parser = argparse.ArgumentParser(
        description="Books_Converter — PDF 转 EPUB 智能转换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python pipeline.py "D:\\books\\scan.pdf"                    # 扫描本
  python pipeline.py "D:\\books\\text.pdf" --no-ocr           # 文字版
  python pipeline.py book.pdf -o F:\\epubs                    # 指定输出
        """,
    )
    parser.add_argument("pdf", help="PDF 文件路径")
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="输出目录 (默认: PDF 所在目录)",
    )
    parser.add_argument(
        "--no-ocr",
        dest="ocr",
        action="store_false",
        help="关闭强制 OCR（用于文字版 PDF）",
    )
    parser.set_defaults(ocr=True)
    parser.add_argument(
        "--skip-mineru",
        action="store_true",
        help="跳过 MinerU 阶段（使用已有结果）",
    )
    parser.add_argument(
        "--skip-deepseek",
        action="store_true",
        help="跳过 DeepSeek 结构分析（使用已有 structure.json）",
    )

    args = parser.parse_args()
    pdf_path = Path(args.pdf)

    if not pdf_path.exists():
        logger.error(f"PDF 文件不存在: {pdf_path}")
        sys.exit(1)

    book_name = pdf_path.stem
    # 默认输出到 PDF 所在目录
    output_base = Path(args.output_dir) if args.output_dir else pdf_path.parent
    work_dir = output_base / book_name
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info(f"  Books_Converter")
    logger.info(f"  输入: {pdf_path}")
    logger.info(f"  书名: {book_name}")
    logger.info(f"  工作目录: {work_dir}")
    logger.info(f"  OCR: {'强制' if args.ocr else '自动'}")
    logger.info("=" * 60)

    # ── 启动进度窗口 ──
    pw = ProgressWindow(book_name)
    pw.start()

    total_start = time.time()
    stage_times = {}

    try:
        # ═══ Stage 1: MinerU ══════════════════════════════════════
        mineru_info = None
        if not args.skip_mineru:
            pw.update_stage(1, "MinerU", "正在准备 PDF 解析...")
            t0 = time.time()
            try:
                mineru_info = run_mineru(
                    str(pdf_path), str(work_dir), ocr=args.ocr,
                    progress=lambda detail: pw.update_stage(1, "MinerU", detail),
                )
                save_mineru_metadata(str(work_dir), mineru_info)
            except Exception as e:
                logger.error(f"Stage 1 失败: {e}")
                logger.error("请检查: ① 网络连接 ② API Token 是否有效 ③ PDF 是否损坏")
                sys.exit(1)
            stage_times["MinerU"] = time.time() - t0
            pw.complete_stage(1, "MinerU", stage_times["MinerU"])
        else:
            # 尝试加载已有结果
            logger.info("跳过 Stage 1，使用已有 MinerU 结果")
            mineru_info = _load_mineru_cache(work_dir)
            if not mineru_info:
                logger.error("未找到已有 MinerU 结果，请先运行 Stage 1")
                sys.exit(1)
            pw.complete_stage(1, "MinerU (缓存)", 0)

        # ═══ Stage 2: DeepSeek 结构分析 ════════════════════════════
        structure = None
        if not args.skip_deepseek:
            pw.update_stage(2, "DeepSeek", "正在准备语义分析...")
            t0 = time.time()
            try:
                structure = analyze_structure(
                    mineru_info["markdown"],
                    mineru_info["content_list"],
                    book_name,
                    progress=lambda detail: pw.update_stage(2, "DeepSeek", detail),
                )
                save_structure(structure, str(work_dir))
            except Exception as e:
                logger.error(f"Stage 2 失败: {e}")
                logger.error("将使用 MinerU 原始结构继续生成 EPUB...")
                structure = _fallback_structure(mineru_info, book_name)
            stage_times["DeepSeek"] = time.time() - t0
            pw.complete_stage(2, "DeepSeek", stage_times["DeepSeek"])
        else:
            structure_path = work_dir / "structure.json"
            if structure_path.exists():
                import json
                with open(structure_path, "r", encoding="utf-8") as f:
                    structure = json.load(f)
                logger.info(f"加载已有结构分析: {structure_path}")
            else:
                logger.error("未找到 structure.json，请先运行 Stage 2")
                sys.exit(1)
            pw.complete_stage(2, "DeepSeek (缓存)", 0)

        # ═══ Stage 3: EPUB 生成 ════════════════════════════════════
        pw.update_stage(3, "EPUB 生成", "渲染章节 HTML、构建嵌套 TOC、打包...")
        t0 = time.time()
        try:
            epub_path = generate_epub(
                book_name,
                mineru_info,
                structure,
                str(work_dir),
                pdf_path=str(pdf_path),
            )
            # 复制一份到 PDF 同目录（方便使用）
            final_path = pdf_path.parent / epub_path.name
            if epub_path != final_path:
                import shutil
                shutil.copy2(epub_path, final_path)
                logger.info(f"  EPUB 已复制到: {final_path}")
                epub_path = final_path
        except Exception as e:
            logger.error(f"Stage 3 失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        stage_times["EPUB"] = time.time() - t0
        pw.complete_stage(3, "EPUB 生成", stage_times["EPUB"])

        # ═══ 完成 ═════════════════════════════════════════════
        total_elapsed = time.time() - total_start
        epub_size_kb = epub_path.stat().st_size / 1024 if epub_path.exists() else 0

        logger.info("")
        logger.info("=" * 60)
        logger.info(f"  ✅ 转换完成!")
        logger.info(f"  📕 EPUB: {epub_path} ({epub_size_kb:.0f} KB)")
        logger.info("")
        logger.info(f"  ⏱  耗时摘要:")
        for stage, t in stage_times.items():
            logger.info(f"     {stage:<12} {t:.0f}s")
        logger.info(f"     {'总计':<12} {total_elapsed:.0f}s")
        logger.info("=" * 60)

        pw.finish(str(epub_path.name), total_elapsed)

        # 声音提示
        try:
            import winsound
            winsound.Beep(1000, 500)
        except Exception:
            pass

        return epub_path

    except SystemExit:
        pw.close()
        raise
    except KeyboardInterrupt:
        pw.close()
        raise


def _load_mineru_cache(work_dir: Path) -> dict | None:
    """加载已有的 MinerU 结果"""
    mineru_dir = work_dir / "mineru"
    md_files = list(mineru_dir.glob("*.md"))
    if not md_files:
        return None

    md_path = md_files[0]
    with open(md_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    # 尝试加载 content_list
    content_list = []
    cl_files = list(mineru_dir.glob("*content_list*.json"))
    if cl_files:
        import json
        with open(cl_files[0], "r", encoding="utf-8") as f:
            content_list = json.load(f)

    images_dir = mineru_dir / "images"
    logger.info(f"  加载已有缓存: {len(markdown):,} 字符 markdown")

    return {
        "markdown": markdown,
        "content_list": content_list,
        "images_dir": str(images_dir) if images_dir.exists() else "",
    }


def _fallback_structure(mineru_info: dict, book_name: str) -> dict:
    """当 DeepSeek 不可用时，基于 MinerU text_level 生成基础结构"""
    content_list = mineru_info.get("content_list", [])
    chapters = []
    current_chapter = None

    for block in content_list:
        level = block.get("text_level", 0)
        page = block.get("page_idx", 0) + 1

        if level >= 1:
            if current_chapter:
                chapters.append(current_chapter)
            current_chapter = {
                "type": "chapter",
                "title": block.get("text", f"章节 {len(chapters)+1}"),
                "level": level,
                "page_start": page,
                "page_end": page,
            }
        elif current_chapter:
            current_chapter["page_end"] = page

    if current_chapter:
        chapters.append(current_chapter)

    logger.info(f"  降级结构: 基于字号检测到 {len(chapters)} 个章节")
    return {
        "metadata": {"title": book_name, "authors": [], "translator": None, "publisher": None, "language": "zh"},
        "front_matter": [],
        "body": chapters,
        "back_matter": [],
        "noise_ranges": [],
    }


if __name__ == "__main__":
    main()
