"""
Stage 1: MinerU API — PDF → 结构化 Markdown + JSON + 图片
支持超大 PDF 自动分片，绕过免费 API 的页数限制。
"""

import json
import logging
import time
from pathlib import Path

import fitz  # PyMuPDF, for page counting
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

# 每片最大页数（免费 API 建议值）
CHUNK_SIZE = 200


def _count_pages(pdf_path: str) -> int:
    """获取 PDF 总页数"""
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


def run_mineru(pdf_path: str, output_dir: str, ocr: bool = True,
               progress=None) -> dict:
    """
    调用 MinerU API 解析 PDF，超大文件自动分片。

    Args:
        progress: 可选回调函数，接收字符串描述当前进度
    """
    pdf_path = Path(pdf_path)
    book_name = pdf_path.stem
    mineru_out = Path(output_dir) / "mineru"
    mineru_out.mkdir(parents=True, exist_ok=True)

    total_pages = _count_pages(str(pdf_path))
    pdf_size_mb = pdf_path.stat().st_size / (1024 * 1024)
    chunks_needed = (total_pages + CHUNK_SIZE - 1) // CHUNK_SIZE

    logger.info(f"Stage 1: MinerU 解析 '{pdf_path.name}'")
    logger.info(f"  文件: {pdf_size_mb:.1f} MB, {total_pages} 页, "
                f"分 {chunks_needed} 片 (每片 {CHUNK_SIZE} 页), OCR={'强制' if ocr else '自动'}")

    all_markdown = []
    all_blocks = []
    all_images = {}
    page_offset = 0
    _report = progress or (lambda *a, **kw: None)

    client = MinerU(MINERU_TOKEN)
    try:
        for chunk_idx in range(chunks_needed):
            start_page = chunk_idx * CHUNK_SIZE + 1
            end_page = min(start_page + CHUNK_SIZE - 1, total_pages)
            page_range = f"{start_page}-{end_page}"

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
                    result = client.extract(
                        str(pdf_path),
                        model=MINERU_MODEL,
                        ocr=ocr,
                        formula=MINERU_ENABLE_FORMULA,
                        table=MINERU_ENABLE_TABLE,
                        language=MINERU_LANGUAGE,
                        pages=page_range,
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
                logger.error(f"    片 {chunk_idx + 1} 重试 3 次后仍失败: {last_error}")
                raise last_error

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
            page_offset += (end_page - start_page + 1)

            # 保存图片到磁盘（MinerU SDK 以 bytes 形式返回）
            if result.images:
                images_out = mineru_out / "images"
                images_out.mkdir(parents=True, exist_ok=True)
                for img in result.images:
                    img_file = images_out / img.name
                    with open(img_file, "wb") as f:
                        f.write(img.data)

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
