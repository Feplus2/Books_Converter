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
import re
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
from stage2_vlm import analyze_structure_vlm
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
        "--format",
        dest="formats",
        default="epub",
        metavar="FMT[,FMT…]",
        help="导出格式多选：epub,md,tex（逗号分隔，默认 epub）",
    )
    parser.add_argument(
        "--md-split",
        action="store_true",
        help="Markdown 按章拆分（index.md + chapters/；默认单文件）",
    )
    parser.add_argument(
        "--md-dialect",
        choices=["gfm", "pandoc"],
        default="gfm",
        help="Markdown 方言（默认 gfm）",
    )
    parser.add_argument(
        "--tex-fragment",
        dest="tex_full",
        action="store_false",
        help="TeX 只产片段（默认完整可编译文档）",
    )
    parser.set_defaults(tex_full=True)
    parser.add_argument(
        "--export-lang",
        choices=["auto", "orig", "trans", "both"],
        default="auto",
        help="导出语言：auto=有译文用译文；orig=原文；trans=译文；both=双出（默认 auto）",
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
        # MinerU 两书实测均值 ≈ 0.80 s/页；PaddleOCR 实测更快；
        # VLM（glm-5.3-flash/low，workers=4）T6 实测 ≈ 3 s/页
        est_s1 = max(total_pages * (0.80 if engine == "mineru"
                                    else 3.0 if engine == "vlm" else 0.50), 30)
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
        # ═══ Stage 1: 解析引擎（MinerU / PaddleOCR / VLM） ═════════════
        s1_name = {"mineru": "MinerU", "paddleocr": "PaddleOCR",
                   "vlm": "VLM"}.get(engine, engine)
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

        # ═══ Stage 2: 结构分析（Hybrid / VLM-Structure） ═══════════════
        structure = None
        if not args.skip_deepseek:
            # PDF outline/书签先验已弃用（病例 028，用户拍板 2026-09-09）：
            # 扫描本第三方书签形态不可控（假目录/泛名条目/页码书签洪水三连），
            # 只信 OCR 重建目录 + 前后页词表锚定。_read_pdf_outline 保留备查。
            pdf_toc = None
            s2_name = "VLM-Structure" if engine == "vlm" else "Hybrid"
            pw.update_stage(2, s2_name, "正在准备结构分析...")
            t0 = time.time()
            try:
                if engine == "vlm":
                    if args.max_pages:
                        logger.warning("--max-pages 仅 Hybrid 路径支持，VLM 路径忽略")
                    vlm_db = work_dir / "vlm" / "vlm_state.db"
                    structure = analyze_structure_vlm(
                        mineru_info["content_list"],
                        book_name,
                        str(work_dir),
                        vlm_state_db=vlm_db if vlm_db.exists() else None,
                        progress=lambda detail, fraction=None: pw.update_stage(2, s2_name, detail, fraction),
                    )
                else:
                    structure = analyze_structure_hybrid(
                        mineru_info["content_list"],
                        book_name,
                        str(work_dir),
                        progress=lambda detail, fraction=None: pw.update_stage(2, s2_name, detail, fraction),
                        max_pages=args.max_pages,
                        pdf_toc=pdf_toc,
                    )
                save_structure(structure, str(work_dir))
            except Exception as e:
                logger.error(f"Stage 2 ({s2_name}) 失败: {e}")
                logger.error("将使用 MinerU 原始结构继续生成 EPUB...")
                structure = _fallback_structure(mineru_info, book_name)
            stage_times[s2_name] = time.time() - t0
            pw.complete_stage(2, s2_name, stage_times[s2_name])
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
        products: dict[str, list[str]] = {}
        try:
            epub_path = generate_epub(
                book_name,
                mineru_info,
                structure,
                str(work_dir),
                pdf_path=str(pdf_path),
                translations=translations,
            )
            # 复制产物到输出目录：<输出>/<书名>/<格式>/ 每格式自含子目录
            # （md/tex 含 images/；分章 md 目录内容平铺进格式目录）
            import shutil

            deliver_name = _unique_book_dir(output_base, book_name)
            if deliver_name != book_name:
                logger.info(f"  输出目录撞名避让: {book_name}/ → {deliver_name}/")

            def _deliver(fmt: str, paths: list, with_images: bool) -> list[str]:
                tgt = output_base / deliver_name / fmt
                tgt.mkdir(parents=True, exist_ok=True)
                delivered: list[str] = []
                for p in paths:
                    p = Path(p)
                    if p.is_dir():
                        for child in p.iterdir():
                            dst = tgt / child.name
                            if child.is_dir():
                                if dst.exists():
                                    shutil.rmtree(dst)
                                shutil.copytree(child, dst)
                            else:
                                shutil.copy2(child, dst)
                        delivered.append(str(tgt))
                    else:
                        dst = tgt / p.name
                        if p != dst:
                            shutil.copy2(p, dst)
                        delivered.append(str(dst))
                if with_images:
                    src_images = work_dir / "images"
                    if src_images.is_dir():
                        # 永远刷新：老产物残留会让新引用缺图（高数 p0022_1 实测——
                        # 此前 tgt/images 已存在即跳过，新跑图片根本没进交付目录）
                        dst = tgt / "images"
                        if dst.exists():
                            shutil.rmtree(dst)
                        shutil.copytree(src_images, dst)
                return delivered

            epub_path = Path(_deliver("epub", [epub_path], with_images=False)[0])
            logger.info(f"  EPUB 已复制到: {epub_path}")
            products["epub"] = [str(epub_path)]
            # ── 平行导出（Markdown/TeX，--format 多选）──
            extra_formats = {f.strip() for f in str(args.formats).split(",")} - {"epub", ""}
            if extra_formats:
                try:
                    from stage3_export import export_book
                    produced = export_book(
                        str(work_dir), extra_formats,
                        md_split=args.md_split, md_dialect=args.md_dialect,
                        tex_full=args.tex_full, export_lang=args.export_lang,
                        progress=lambda d, f=None: pw.update_stage(s_epub, "EPUB 生成", d, f),
                    )
                    for fmt, paths in produced.items():
                        delivered = _deliver(fmt, paths, with_images=True)
                        logger.info(f"  {fmt.upper()} 已复制到: {delivered}")
                        products.setdefault(fmt, []).extend(delivered)
                except Exception as e:
                    logger.error(f"导出 {sorted(extra_formats)} 失败（EPUB 不受影响）: {e}")
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
        from version import __version__
        _register_product(output_base, {
            "v": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "title": book_name,
            "dir_name": deliver_name,
            "source_pdf": str(pdf_path),
            "work_dir": str(work_dir),
            "engine": engine,
            "ocr": bool(args.ocr),
            "translate": args.translate,
            "vlm_model": config.VLM_MODEL if engine == "vlm" else None,
            "vlm_reasoning": config.VLM_REASONING if engine == "vlm" else None,
            "formats": sorted(products.keys()),
            "products": products,
            "elapsed_s": round(total_elapsed, 1),
            "app_version": __version__,
        })
        pw.finish(str(epub_path) if args.headless else str(epub_path.name), total_elapsed)

        # 完成提示音（异步 WAV；CONVERT_COMPLETE_SOUND=off 可关）
        from completion_sound import play_completion_sound
        play_completion_sound()

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


_PRODUCT_DIRS = ("epub", "md", "tex")


def _unique_book_dir(output_base: Path, book_name: str) -> str:
    """输出目录撞名避让：已有交付产物（epub/md/tex 任一非空）→ 书名 (1)/(2)…

    用户裁定：同本书重转到同一输出目录不得覆盖旧产物（引擎/选项可能不同，
    旧产物有对照价值）。注意 work_dir 与交付根同为 <输出>/<书名>/——目录里
    只有缓存（vlm_state.db/structure.json 等中间产物）不算撞名，重跑复用
    缓存并照常交付；只有旧交付产物存在时才避让。
    """
    def _has_products(d: Path) -> bool:
        for fmt in _PRODUCT_DIRS:
            sub = d / fmt
            try:
                if sub.is_dir() and any(sub.iterdir()):
                    return True
            except OSError:
                pass
        return False

    cand = output_base / book_name
    if not cand.exists() or not _has_products(cand):
        return book_name
    for i in range(1, 1000):
        name = f"{book_name} ({i})"
        c = output_base / name
        if not c.exists() or not _has_products(c):
            return name
    raise RuntimeError(f"无法为《{book_name}》分配输出目录（序号耗尽）")


def _register_product(output_base: Path, record: dict) -> None:
    """产物登记处：每完成一本向 <输出目录>/_registry.jsonl 追加一行。

    这是 GUI 产物库（wiki/08）的数据源。失败方向 = 不动作：
    登记失败只告警，绝不影响转换主流程。
    """
    import json
    try:
        output_base.mkdir(parents=True, exist_ok=True)
        with open(output_base / "_registry.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"产物登记失败（不影响产物）: {e}")


def _read_pdf_outline(pdf_path: Path) -> list:
    """PDF outline/书签 → toc_entries 格式的目录先验。

    ⚠️ 已弃用（病例 028）：扫描本第三方书签形态不可控（'标题／页码'假目录、
    泛名 'Chapter N'、页码书签洪水），调用方已于 2026-09-09 停用。
    函数保留备查/调试；若要恢复，先过 _sanitize_pdf_toc 再注入。
    """
    try:
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            toc = doc.get_toc(simple=True)
    except Exception as e:
        logger.warning(f"读取 PDF 书签失败: {e}")
        return []
    out = []
    for lv, title, page in toc:
        title = re.sub(r"\s+", " ", str(title)).strip()
        if title and page >= 1:
            out.append({"text": title, "level": max(int(lv), 1),
                        "page": int(page)})
    return out


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
