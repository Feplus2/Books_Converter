"""
Stage 1: MinerU API — PDF → 结构化 Markdown + JSON + 图片
超大 PDF 物理切片上传：页数天花板 + 字节天花板，哪个先到切哪刀。
"""

import json
import logging
import time
from pathlib import Path

import fitz  # PyMuPDF, for page counting & 物理切片
from mineru import MinerU

from config import (
    MINERU_TOKEN,
    MINERU_MODEL,
    MINERU_LANGUAGE,
    MINERU_TIMEOUT,
    MINERU_ENABLE_FORMULA,
    MINERU_ENABLE_TABLE,
)

logger = logging.getLogger(__name__)

# 双天花板（云端硬限制：单文件 ≤200MB 且 ≤600 页，SDK FileTooLargeError/
# PageLimitError 实证）。字节取 200MB 的 75 折留余量；页数 200 为免费档建议值。
CHUNK_MAX_PAGES = 200
CHUNK_MAX_BYTES = 150 * 1024 * 1024


def _count_pages(pdf_path: str) -> int:
    """获取 PDF 总页数"""
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


def _write_chunk(doc: fitz.Document, start: int, end: int, out_path: Path) -> int:
    """把 [start, end]（0-based 闭区间）写成独立 chunk 文件，返回真实字节数。"""
    sub = fitz.open()
    sub.insert_pdf(doc, from_page=start, to_page=end)
    sub.save(str(out_path), deflate=True, garbage=3)
    sub.close()
    return out_path.stat().st_size


def _downsample_single_page(doc: fitz.Document, pno: int, out_path: Path,
                            max_bytes: int) -> int:
    """单页超字节天花板的极端高清扫描页：光栅化降采样重嵌（明示日志）。

    文本层丢失属可控代价——这种页本来就是整页扫描图。逐档降 dpi 直到
    低于阈值（失败方向：最低档仍超也照交，云端报错信息带页码）。"""
    rect = doc[pno].rect
    sz = 0
    for dpi in (200, 150, 100):
        pix = doc[pno].get_pixmap(dpi=dpi)
        if pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        img_doc = fitz.open()
        page = img_doc.new_page(width=rect.width, height=rect.height)
        # 扫描页本质是照片：JPEG q80 重嵌（体积比 PNG 小一个量级）
        page.insert_image(rect, stream=pix.tobytes("jpeg", jpg_quality=80))
        img_doc.save(str(out_path), deflate=True, garbage=3)
        img_doc.close()
        sz = out_path.stat().st_size
        if sz <= max_bytes:
            logger.warning(f"  第 {pno + 1} 页单页超阈值，已降采样为 {dpi}dpi "
                           f"JPEG 图像页（{sz / 2 ** 20:.1f}MB）")
            return sz
    logger.warning(f"  第 {pno + 1} 页降采样到 100dpi 仍超阈值"
                   f"（{sz / 2 ** 20:.1f}MB），照交云端判定")
    return sz


def _split_pdf_chunks(pdf_path: Path, tmp_dir: Path,
                      max_pages: int = CHUNK_MAX_PAGES,
                      max_bytes: int = CHUNK_MAX_BYTES
                      ) -> list[tuple[Path, int, int]]:
    """物理切片：页数将达 max_pages 或片字节将达 max_bytes 即切一刀。

    返回 [(chunk_path, start0, end0), ...]（0-based 闭区间，保序）。
    实测驱动：先按页数切，每片写实测字节，超阈值对半递归；单页仍超 →
    降采样。整本书本来就达标 → 直接用原文件，零拷贝。
    """
    doc = fitz.open(str(pdf_path))
    total = doc.page_count
    try:
        if total <= max_pages and pdf_path.stat().st_size <= max_bytes:
            return [(pdf_path, 0, total - 1)]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[tuple[Path, int, int]] = []

        def carve(start: int, end: int) -> None:
            n = end - start + 1
            if n > max_pages:
                mid = start + max_pages - 1
                carve(start, mid)
                carve(mid + 1, end)
                return
            path = tmp_dir / f"_chunk_{len(chunks):03d}_{start + 1}-{end + 1}.pdf"
            sz = _write_chunk(doc, start, end, path)
            if sz <= max_bytes:
                chunks.append((path, start, end))
                return
            if n == 1:
                _downsample_single_page(doc, start, path, max_bytes)
                chunks.append((path, start, end))
                return
            path.unlink()  # 这片超字节，重写为两半
            mid = start + n // 2 - 1
            carve(start, mid)
            carve(mid + 1, end)

        carve(0, total - 1)
        if len(chunks) > 1:
            sizes = " / ".join(f"{p.name.rsplit('_', 1)[-1]}:"
                               f"{p.stat().st_size / 2 ** 20:.0f}MB"
                               for p, _, _ in chunks[:8])
            logger.info(f"  物理切片 {len(chunks)} 片（双天花板 {max_pages} 页/"
                        f"{max_bytes // 2 ** 20}MB）：{sizes}"
                        f"{' …' if len(chunks) > 8 else ''}")
        return chunks
    finally:
        doc.close()


def run_mineru(pdf_path: str, output_dir: str, ocr: bool = True,
               progress=None) -> dict:
    """
    调用 MinerU API 解析 PDF，超大文件物理切片上传。

    Args:
        progress: 可选回调函数，接收字符串描述当前进度
    """
    pdf_path = Path(pdf_path)
    book_name = pdf_path.stem
    mineru_out = Path(output_dir) / "mineru"
    mineru_out.mkdir(parents=True, exist_ok=True)

    total_pages = _count_pages(str(pdf_path))
    pdf_size_mb = pdf_path.stat().st_size / (1024 * 1024)

    logger.info(f"Stage 1: MinerU 解析 '{pdf_path.name}'")
    logger.info(f"  文件: {pdf_size_mb:.1f} MB, {total_pages} 页, "
                f"OCR={'强制' if ocr else '自动'}")

    chunks = _split_pdf_chunks(pdf_path, mineru_out / "_chunks")
    chunks_needed = len(chunks)
    logger.info(f"  分 {chunks_needed} 片上传（双天花板 "
                f"{CHUNK_MAX_PAGES} 页/{CHUNK_MAX_BYTES // 2 ** 20}MB）")

    all_markdown = []
    all_blocks = []
    page_offset = 0
    _report = progress or (lambda *a, **kw: None)

    client = MinerU(MINERU_TOKEN)
    try:
        for chunk_idx, (chunk_path, start0, end0) in enumerate(chunks):
            start_page, end_page = start0 + 1, end0 + 1  # 1-based 展示
            n_pages = end0 - start0 + 1

            _report(f"片 {chunk_idx + 1}/{chunks_needed}: 第 {start_page}-{end_page} 页 正在上传...",
                    chunk_idx / chunks_needed)
            logger.info(f"  片 {chunk_idx + 1}/{chunks_needed}: "
                        f"第 {start_page}-{end_page} 页 ...")
            t0 = time.time()

            # 重试逻辑：处理间歇性 SSL/CDN 错误
            result = None
            last_error = None
            for attempt in range(3):
                try:
                    if attempt > 0:
                        _report(f"片 {chunk_idx + 1}/{chunks_needed}: 重试 {attempt + 1}/3...")
                    # 物理切片后废弃 pages 参数：逐 chunk 全量解析
                    result = client.extract(
                        str(chunk_path),
                        model=MINERU_MODEL,
                        ocr=ocr,
                        formula=MINERU_ENABLE_FORMULA,
                        table=MINERU_ENABLE_TABLE,
                        language=MINERU_LANGUAGE,
                        timeout=MINERU_TIMEOUT,
                    )
                    break
                except Exception as e:
                    last_error = e
                    if attempt < 2:
                        wait = (attempt + 1) * 10
                        logger.warning(f"    尝试 {attempt + 1} 失败，{wait}s 后重试: {e}")
                        time.sleep(wait)
            if result is None:
                raise RuntimeError(
                    f"MinerU 片 {chunk_idx + 1}/{chunks_needed} "
                    f"（第 {start_page}-{end_page} 页）重试 3 次仍失败: {last_error}")

            elapsed = time.time() - t0
            if result.state != "done":
                raise RuntimeError(f"片 {chunk_idx + 1} 失败: state={result.state}")

            md_chunk = result.markdown or ""
            blocks_chunk = result.content_list or []

            # 调整 page_idx: MinerU 的 page_idx 从 0 开始且相对当前 chunk
            for block in blocks_chunk:
                if "page_idx" in block:
                    block["page_idx"] = block["page_idx"] + page_offset

            all_markdown.append(md_chunk)
            all_blocks.extend(blocks_chunk)
            page_offset += n_pages

            # 保存图片到磁盘（MinerU SDK 以 bytes 形式返回）
            if result.images:
                images_out = mineru_out / "images"
                images_out.mkdir(parents=True, exist_ok=True)
                for img in result.images:
                    img_file = images_out / img.name
                    with open(img_file, "wb") as f:
                        f.write(img.data)

            # chunk 临时文件随用随清（大书切片体积可观）
            if chunk_path != pdf_path:
                try:
                    chunk_path.unlink()
                except OSError:
                    pass

            _report(f"片 {chunk_idx + 1}/{chunks_needed}: 完成 — "
                    f"{len(md_chunk):,} 字符, {len(result.images)} 张图片",
                    (chunk_idx + 1) / chunks_needed)
            logger.info(f"    完成: {len(md_chunk):,} 字符, "
                        f"{len(blocks_chunk)} blocks, "
                        f"{len(result.images)} 张图片, 耗时 {elapsed:.0f}s")

        # 合并 markdown
        merged_md = "\n\n".join(all_markdown)

        # 保存到 mineru 输出目录
        md_path = mineru_out / f"{book_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(merged_md)

        cl_path = mineru_out / f"{book_name}_content_list.json"
        with open(cl_path, "w", encoding="utf-8") as f:
            json.dump(all_blocks, f, ensure_ascii=False, indent=2)

        logger.info(f"  合并完成: {len(merged_md):,} 字符 markdown, "
                    f"{len(all_blocks)} 个内容块")

        return {
            "markdown": merged_md,
            "content_list": all_blocks,
            "images_dir": str(mineru_out / "images"),
        }

    finally:
        client.close()
        # 收尾清理残留切片（失败中断时也尽量清；目录非空则留现场供排查）
        chunks_dir = mineru_out / "_chunks"
        if chunks_dir.is_dir():
            try:
                for leftover in chunks_dir.glob("_chunk_*.pdf"):
                    leftover.unlink()
                chunks_dir.rmdir()
            except OSError:
                pass


def save_mineru_metadata(output_dir: str, info: dict) -> None:
    """保存 MinerU 阶段的元数据"""
    meta = {
        "task_id": info.get("task_id"),
        "markdown_length": len(info.get("markdown", "")),
        "content_blocks": len(info.get("content_list", [])),
        "images_dir": info.get("images_dir"),
    }
    meta_path = Path(output_dir) / "mineru" / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
