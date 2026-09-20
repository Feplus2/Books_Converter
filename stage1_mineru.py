"""
Stage 1: MinerU API — PDF → 结构化 Markdown + JSON + 图片
超大 PDF 物理切片上传：页数天花板 + 字节天花板，哪个先到切哪刀。
"""

import json
import logging
import os
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


class _CloudTimeout(Exception):
    """云端轮询超时（任务排队/龟速）——与网络抖动区分：上层对半拆，不睡重试。"""


def _cloud_err_detail(result) -> str:
    """SDK ExtractResult 的云端错误详情（MinerU SDK：err_code/err_msg→error，
    见 mineru/client.py）。有才返回「 err_code=… err_msg=…」，没有/超时
    （result=None）返回空串——失败方向=不动作，绝不编造。"""
    if result is None:
        return ""
    parts = []
    code = getattr(result, "err_code", "") or ""
    msg = getattr(result, "error", None) or ""
    if code:
        parts.append(f"err_code={code}")
    if msg:
        parts.append(f"err_msg={msg}")
    return f"（{' '.join(parts)}）" if parts else ""

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
                      max_bytes: int = CHUNK_MAX_BYTES,
                      run_tag: str | None = None
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
        # 文件名带进程号：同工作目录并发跑同一本书时，残留/锁定中的旧切片
        # 不会挡住本次保存（Windows 上 save 覆盖被锁文件 = 'cannot remove
        # file' 直接炸 Stage 1——izuno 双管线互踩实测）。run_tag 由调用方
        # （run_mineru）传入，保证 finally 清理扫的是同一批本进程切片；
        # 未传则自生成（单测直连本函数的场景）。
        if run_tag is None:
            run_tag = f"{os.getpid():x}"

        def carve(start: int, end: int) -> None:
            n = end - start + 1
            if n > max_pages:
                mid = start + max_pages - 1
                carve(start, mid)
                carve(mid + 1, end)
                return
            path = tmp_dir / f"_chunk_{run_tag}_{len(chunks):03d}_{start + 1}-{end + 1}.pdf"
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

    # 本进程切片文件名标识：切片与 finally 收尾清理共用同一 run_tag，
    # 只扫本进程切片（047 语义），绝不动并发跑同书的他进程残留。
    run_tag = f"{os.getpid():x}"

    chunks = _split_pdf_chunks(pdf_path, mineru_out / "_chunks", run_tag=run_tag)
    chunks_needed = len(chunks)
    logger.info(f"  分 {chunks_needed} 片上传（双天花板 "
                f"{CHUNK_MAX_PAGES} 页/{CHUNK_MAX_BYTES // 2 ** 20}MB）")

    all_markdown = []
    all_blocks = []
    failed_pages: list[int] = []
    _report = progress or (lambda *a, **kw: None)

    client = MinerU(MINERU_TOKEN)

    def _extract_once(path: Path):
        """网络层重试 3 次（间歇性 SSL/CDN）；仍败抛 RuntimeError。
        云端轮询超时（任务排队/龟速，FG 实测 50 分钟假象卡死）立刻上抛
        _CloudTimeout——睡重试只会再排一次长队，交上层对半拆才是正解。"""
        last_error = None
        for attempt in range(3):
            try:
                return client.extract(
                    str(path),
                    model=MINERU_MODEL,
                    ocr=ocr,
                    formula=MINERU_ENABLE_FORMULA,
                    table=MINERU_ENABLE_TABLE,
                    language=MINERU_LANGUAGE,
                    timeout=MINERU_TIMEOUT,
                )
            except Exception as e:
                if type(e).__name__ == "TimeoutError":
                    raise _CloudTimeout(str(e)) from e
                last_error = e
                if attempt < 2:
                    wait = (attempt + 1) * 10
                    logger.warning(f"    尝试 {attempt + 1} 失败，{wait}s 后重试: {e}")
                    time.sleep(wait)
        raise RuntimeError(f"重试 3 次仍失败: {last_error}")

    def _extract_chunk(chunk_path: Path, start0: int, end0: int,
                       tag: str) -> list[tuple]:
        """单片解析。云端 state=failed（解析失败——密度/坏页压垮 worker，
        机械手册实测 200 页/34MB 与 20 页密扫皆败、10 页即过）或轮询超时
        时对半递归；单页仍败 → 记入 failed_pages 跳过（缺口优于陪葬全书）。
        返回 [(result, sub_start0), ...]（保序）。"""
        try:
            result = _extract_once(chunk_path)
        except _CloudTimeout:
            result = None
        if result is not None and result.state == "done":
            return [(result, start0)]
        if start0 == end0:
            why = "解析失败" if result is not None else "轮询超时"
            logger.error(f"    第 {start0 + 1} 页云端{why}"
                         f"{_cloud_err_detail(result)}，"
                         f"跳过该页（内容缺口，QC 缺页检查会报）")
            failed_pages.append(start0)
            return []
        mid = start0 + (end0 - start0) // 2
        why = "云端解析失败" if result is not None else "云端轮询超时"
        logger.warning(f"    {tag}（第 {start0 + 1}-{end0 + 1} 页）{why}"
                       f"{_cloud_err_detail(result)}，"
                       f"对半拆为 {start0 + 1}-{mid + 1} / {mid + 2}-{end0 + 1} 重试")
        _report(f"{tag}: {why}，对半拆分重试…", None)
        doc = fitz.open(str(chunk_path))
        out: list[tuple] = []
        try:
            for lo, hi, suffix in ((start0, mid, "a"), (mid + 1, end0, "b")):
                sub_path = chunk_path.with_name(
                    f"{chunk_path.stem}_{suffix}{lo + 1}-{hi + 1}.pdf")
                # chunk 文件内页码是 0 基相对的：lo/hi 映射回文件内区间
                _write_chunk(doc, lo - start0, hi - start0, sub_path)
                out.extend(_extract_chunk(sub_path, lo, hi, tag + suffix))
                try:
                    sub_path.unlink()
                except OSError:
                    pass
        finally:
            doc.close()
        return out

    try:
        for chunk_idx, (chunk_path, start0, end0) in enumerate(chunks):
            start_page, end_page = start0 + 1, end0 + 1  # 1-based 展示

            _report(f"片 {chunk_idx + 1}/{chunks_needed}: 第 {start_page}-{end_page} 页 正在上传...",
                    chunk_idx / chunks_needed)
            logger.info(f"  片 {chunk_idx + 1}/{chunks_needed}: "
                        f"第 {start_page}-{end_page} 页 ...")
            t0 = time.time()

            results = _extract_chunk(chunk_path, start0, end0,
                                     f"片 {chunk_idx + 1}/{chunks_needed}")

            elapsed = time.time() - t0
            n_md = n_blk = n_img = 0
            for result, sub_start0 in results:
                md_chunk = result.markdown or ""
                blocks_chunk = result.content_list or []

                # 调整 page_idx: MinerU 从 0 开始且相对当前（子）片
                for block in blocks_chunk:
                    if "page_idx" in block:
                        block["page_idx"] = block["page_idx"] + sub_start0

                all_markdown.append(md_chunk)
                all_blocks.extend(blocks_chunk)
                n_md += len(md_chunk)
                n_blk += len(blocks_chunk)

                # 保存图片到磁盘（MinerU SDK 以 bytes 形式返回）
                if result.images:
                    images_out = mineru_out / "images"
                    images_out.mkdir(parents=True, exist_ok=True)
                    for img in result.images:
                        img_file = images_out / img.name
                        with open(img_file, "wb") as f:
                            f.write(img.data)
                        n_img += 1

            # chunk 临时文件随用随清（大书切片体积可观）
            if chunk_path != pdf_path:
                try:
                    chunk_path.unlink()
                except OSError:
                    pass

            _report(f"片 {chunk_idx + 1}/{chunks_needed}: 完成 — "
                    f"{n_md:,} 字符, {n_img} 张图片",
                    (chunk_idx + 1) / chunks_needed)
            logger.info(f"    完成: {n_md:,} 字符, {n_blk} blocks, "
                        f"{n_img} 张图片, 耗时 {elapsed:.0f}s")

        # 合并 markdown
        merged_md = "\n\n".join(all_markdown)

        # 保存到 mineru 输出目录
        md_path = mineru_out / f"{book_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(merged_md)

        cl_path = mineru_out / f"{book_name}_content_list.json"
        with open(cl_path, "w", encoding="utf-8") as f:
            json.dump(all_blocks, f, ensure_ascii=False, indent=2)

        if failed_pages:
            logger.warning(f"  {len(failed_pages)} 页云端解析失败已跳过: "
                           f"{[p + 1 for p in failed_pages][:20]}"
                           f"{' …' if len(failed_pages) > 20 else ''}")

        logger.info(f"  合并完成: {len(merged_md):,} 字符 markdown, "
                    f"{len(all_blocks)} 个内容块")

        return {
            "markdown": merged_md,
            "content_list": all_blocks,
            "images_dir": str(mineru_out / "images"),
            "failed_pages": failed_pages,
        }

    finally:
        client.close()
        # 收尾清理残留切片（失败中断时也尽量清；目录非空则留现场供排查）
        chunks_dir = mineru_out / "_chunks"
        if chunks_dir.is_dir():
            try:
                for leftover in chunks_dir.glob(f"_chunk_{run_tag}_*.pdf"):
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
