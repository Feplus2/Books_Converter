#!/usr/bin/env python3
r"""
Books_Converter — PDF → EPUB 全自动转换管线

用法:
    python pipeline.py <pdf_path> [--engine mineru|paddleocr] [--output-dir <dir>] [--ocr/--no-ocr] [--headless]

示例:
    python pipeline.py "D:\books\mybook.pdf"
    python pipeline.py "D:\books\mybook.pdf" --engine paddleocr
    python pipeline.py "D:\books\mybook.pdf" --output-dir "F:\output"
    python pipeline.py "D:\books\mybook.pdf" --no-ocr   # 文字版 PDF
    python pipeline.py "D:\books\mybook.pdf" --headless # 无界面 JSON 进度（SageRead sidecar）
    python pipeline.py --check-update                   # 检查新版本
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")

from pathlib import Path

from stage1_mineru import _count_pages
from ocr_provider import get_provider, provider_names
from stage2_hybrid import analyze_structure_hybrid, save_structure
from stage3_epub import generate_epub
from progress_headless import HeadlessProgress, emit_error
# ProgressWindow（tkinter）改为延迟导入，headless CLI 不打包 tkinter


class _ErrCapture(logging.Handler):
    """捕获首条 ERROR 日志，作为 headless 模式的错误详情回传（首条通常最贴近根因）"""

    first = ""

    def emit(self, record):
        if record.levelno >= logging.ERROR and not _ErrCapture.first:
            _ErrCapture.first = record.getMessage()


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
    parser.add_argument("pdf", nargs="?", help="PDF 文件路径")
    parser.add_argument(
        "--engine",
        choices=provider_names(),
        default=None,
        metavar="ENGINE",
        help=f"Stage 1 解析引擎（{'/'.join(provider_names())}；默认读 OCR_PROVIDER 配置）",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号并退出",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="检查 GitHub 是否有新版本并退出",
    )
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
        help="跳过 Stage 1 解析阶段（使用已有 MinerU/PaddleOCR 结果）",
    )
    parser.add_argument(
        "--skip-deepseek",
        action="store_true",
        help="跳过结构分析阶段（使用已有 structure.json）",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="仅调试用：结构分析只处理前 N 页（技术验证切片，大幅缩短时间）",
    )
    parser.add_argument(
        "--translate",
        nargs="?",
        const="zh",
        default=None,
        metavar="LANG",
        help="翻译全书（Stage 4，DeepSeek 分批+上下文）。不带参数默认译为中文",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无界面模式：向 stdout 打印 JSON 进度（供 SageRead sidecar 集成）",
    )

    args = parser.parse_args()

    if args.version:
        from version import __version__
        print(f"Books_Converter v{__version__}")
        sys.exit(0)
    if args.check_update:
        from version import __version__
        from updater import check_for_update
        r = check_for_update()
        if r["status"] == "update_available":
            print(f"发现新版本: v{r['latest']}（当前 v{__version__}）")
            print(f"下载: {r['url']}")
        elif r["status"] == "latest":
            print(f"已是最新版本（v{__version__}）")
        else:
            print(f"检查更新失败: {r['error']}")
        sys.exit(0)

    if not args.pdf:
        parser.error("缺少 PDF 文件路径")
    if args.headless:
        logging.getLogger().addHandler(_ErrCapture())

    pdf_path = Path(args.pdf)

    if not pdf_path.exists():
        logger.error(f"PDF 文件不存在: {pdf_path}")
        if args.headless:
            emit_error(f"PDF 文件不存在: {pdf_path}")
        sys.exit(1)

    # Windows 不允许目录名以空格/点结尾（创建时会被静默剥离，导致
    # 后续按原名 iterdir 找不到目录），统一剔除
    book_name = pdf_path.stem.rstrip(" .") or pdf_path.stem
    # 默认输出到 PDF 所在目录
    output_base = Path(args.output_dir) if args.output_dir else pdf_path.parent
    work_dir = output_base / book_name
    work_dir.mkdir(parents=True, exist_ok=True)

    engine = args.engine or config.OCR_PROVIDER

    logger.info("=" * 60)
    logger.info(f"  Books_Converter")
    logger.info(f"  输入: {pdf_path}")
    logger.info(f"  书名: {book_name}")
    logger.info(f"  工作目录: {work_dir}")
    logger.info(f"  解析引擎: {engine}")
    logger.info(f"  OCR: {'强制' if args.ocr else '自动'}")
    logger.info("=" * 60)

    # ── 启动进度报告（按实测速率预估各阶段耗时，校准进度条） ──
    try:
        total_pages = _count_pages(str(pdf_path))
    except Exception:
        total_pages = 300
    if args.skip_mineru:
        est_s1 = 1.0
    else:
        # MinerU 两书实测均值 ≈ 0.80 s/页；PaddleOCR 实测更快
        est_s1 = max(total_pages * (0.80 if engine == "mineru" else 0.50), 30)
    est_s2 = max(total_pages * 0.14, 15)          # hybrid 两书实测均值 ≈ 0.14 s/页
    # 翻译阶段耗时：~6000 字符/批 × 4 并发（另算，见 stage4）
    est_s3 = max(total_pages * 2.0, 30) if args.translate else 3.0
    est_list = [est_s1, est_s2, est_s3, 3.0] if args.translate else [est_s1, est_s2, 3.0]
    if args.headless:
        pw = HeadlessProgress(book_name, engine="hybrid",
                              stage_estimates=est_list,
                              translate=bool(args.translate))
    else:
        from progress_ui import ProgressWindow  # 延迟导入，headless 模式不触 tkinter
        pw = ProgressWindow(book_name, engine="hybrid",
                            stage_estimates=est_list,
                            translate=bool(args.translate))
    pw.start()

    total_start = time.time()
    stage_times = {}

    try:
        # ═══ Stage 1: 解析引擎（MinerU / PaddleOCR） ══════════════════
        s1_name = {"mineru": "MinerU", "paddleocr": "PaddleOCR"}.get(engine, engine)
        mineru_info = None
        if not args.skip_mineru:
            pw.update_stage(1, s1_name, "正在准备 PDF 解析...")
            t0 = time.time()
            try:
                provider = get_provider(engine)
                mineru_info = provider.parse(
                    str(pdf_path), str(work_dir), ocr=args.ocr,
                    progress=lambda detail, fraction=None: pw.update_stage(1, s1_name, detail, fraction),
                )
                _save_stage1_metadata(str(work_dir), engine, mineru_info)
            except Exception as e:
                logger.error(f"Stage 1 ({engine}) 失败: {e}")
                logger.error("请检查: ① 网络连接 ② API Token 是否有效 ③ PDF 是否损坏")
                sys.exit(1)
            stage_times[s1_name] = time.time() - t0
            pw.complete_stage(1, s1_name, stage_times[s1_name])
        else:
            # 尝试加载已有结果
            logger.info("跳过 Stage 1，使用已有解析结果")
            mineru_info = _load_stage1_cache(work_dir, engine)
            if not mineru_info:
                logger.error("未找到已有解析结果，请先运行 Stage 1")
                sys.exit(1)
            pw.complete_stage(1, f"{s1_name} (缓存)", 0)

        # ═══ Stage 2: 结构分析（Hybrid 引擎） ════════════════════════
        structure = None
        if not args.skip_deepseek:
            pw.update_stage(2, "Hybrid", "正在准备结构分析...")
            t0 = time.time()
            try:
                structure = analyze_structure_hybrid(
                    mineru_info["content_list"],
                    book_name,
                    str(work_dir),
                    progress=lambda detail, fraction=None: pw.update_stage(2, "Hybrid", detail, fraction),
                    max_pages=args.max_pages,
                )
                save_structure(structure, str(work_dir))
            except Exception as e:
                logger.error(f"Stage 2 (Hybrid) 失败: {e}")
                logger.error("将使用 MinerU 原始结构继续生成 EPUB...")
                structure = _fallback_structure(mineru_info, book_name)
            stage_times["Hybrid"] = time.time() - t0
            pw.complete_stage(2, "Hybrid", stage_times["Hybrid"])
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

        # ═══ Stage 4: 全书翻译（可选） ════════════════════════════════
        translations = None
        s_epub = 4 if args.translate else 3
        if args.translate:
            from stage4_translate import translate_book
            pw.update_stage(3, "翻译", "分批翻译中（带上下文与译名表）...")
            t0 = time.time()
            try:
                result = translate_book(
                    mineru_info["content_list"],
                    structure.get("metadata", {}),
                    str(work_dir),
                    target_lang=args.translate,
                    progress=lambda detail, fraction=None: pw.update_stage(3, "翻译", detail, fraction),
                )
                translations = result["translations"]
                if result.get("title_zh"):
                    structure["metadata"]["title"] = result["title_zh"]
                if args.translate == "zh":
                    structure["metadata"]["language"] = "zh"
            except Exception as e:
                logger.error(f"Stage 4 翻译失败: {e}，将输出原文 EPUB")
                translations = None
            stage_times["翻译"] = time.time() - t0
            pw.complete_stage(3, "翻译", stage_times["翻译"])

        # ═══ Stage 3: EPUB 生成 ════════════════════════════════════
        pw.update_stage(s_epub, "EPUB 生成", "渲染章节 HTML、构建嵌套 TOC、打包...")
        t0 = time.time()
        try:
            epub_path = generate_epub(
                book_name,
                mineru_info,
                structure,
                str(work_dir),
                pdf_path=str(pdf_path),
                translations=translations,
            )
            # 复制一份到输出目录（--output-dir 生效；默认即 PDF 所在目录）
            final_path = output_base / epub_path.name
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
        pw.complete_stage(s_epub, "EPUB 生成", stage_times["EPUB"])

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

        # headless 需要完整路径（前端据此读文件入库）；GUI 仍只显示文件名
        pw.finish(str(epub_path) if args.headless else str(epub_path.name), total_elapsed)

        # 声音提示：清脆的上行琶音（C6-E6-G6-C7）
        try:
            import winsound
            for freq, dur in ((1046, 110), (1319, 110), (1568, 110), (2093, 260)):
                winsound.Beep(freq, dur)
        except Exception:
            pass

        return epub_path

    except SystemExit as e:
        pw.close()
        if args.headless and (e.code not in (0, None)):
            emit_error(_ErrCapture.first or "转换失败")
        raise
    except KeyboardInterrupt:
        pw.close()
        if args.headless:
            emit_error("用户取消")
        raise
    except Exception as e:
        pw.close()
        if args.headless:
            emit_error(str(e) or _ErrCapture.first or "转换失败")
        raise


def _save_stage1_metadata(work_dir: str, engine: str, info: dict) -> None:
    """保存 Stage 1 阶段的元数据到 <work_dir>/<engine>/metadata.json"""
    import json
    meta = {
        "provider": engine,
        "markdown_length": len(info.get("markdown", "")),
        "content_blocks": len(info.get("content_list", [])),
        "images_dir": info.get("images_dir"),
    }
    meta_path = Path(work_dir) / engine / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _load_stage1_cache(work_dir: Path, engine: str) -> dict | None:
    """加载已有的 Stage 1 结果：优先 <work_dir>/<engine>/，
    其次任意含 content_list 的引擎子目录（如 mineru/、paddleocr/）。"""
    import json
    candidates = []
    preferred = work_dir / engine
    if preferred.is_dir():
        candidates.append(preferred)
    for d in sorted(work_dir.iterdir()):
        if d.is_dir() and d not in candidates:
            candidates.append(d)

    for d in candidates:
        md_files = list(d.glob("*.md"))
        cl_files = list(d.glob("*content_list*.json"))
        if not md_files or not cl_files:
            continue
        with open(md_files[0], "r", encoding="utf-8") as f:
            markdown = f.read()
        with open(cl_files[0], "r", encoding="utf-8") as f:
            content_list = json.load(f)
        images_dir = d / "images"
        logger.info(f"  加载已有缓存 [{d.name}]: {len(markdown):,} 字符 markdown, "
                    f"{len(content_list)} 个内容块")
        return {
            "markdown": markdown,
            "content_list": content_list,
            "images_dir": str(images_dir) if images_dir.exists() else "",
        }
    return None


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
