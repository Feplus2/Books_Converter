"""Stage 1 PaddleOCR-VL Provider — 百度 AI Studio 异步 job API → 统一解析产物契约。

移植自 Papers_Converter 同名模块，按 Books_Converter 契约改造：
- **bbox 千分位**：探针实测 prunedResult 带 width/height（渲染图像素尺寸），
  block_bbox 为像素 xyxy，换算为契约要求的 0-1000 千分位（popo/convert.py
  会丢弃无 bbox 的块）；
- **大书分片**：API 单任务 ≤1000 页、上传 ≤50MB，用 fitz 按平均页体积
  自适应切片（单片 ≤45MB），合并时 page_idx 加偏移（手法同 stage1_mineru）；
- **Unicode 上下标 → LaTeX**：PaddleOCR-VL 正文用 Unicode 上下标（LiCoO₂、
  Li⁺/Na⁺、H₂O）而非契约的 $...$，逐块转换（数学区外才转，下标小数合并）。

接口（文档 https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5 ）：
- POST {PADDLEOCR_API_URL}（multipart 上传）→ jobId
- GET {PADDLEOCR_API_URL}/{jobId} 轮询 → done 后取 resultUrl.jsonUrl
- JSONL 逐页：result.layoutParsingResults[].{markdown, prunedResult}
- 免费配额 3000 页/天/模型，超限 429 / 错误码 12001

产物（落 <work_dir>/paddleocr/）：{stem}.md、{stem}_content_list.json、images/
"""

import json
import logging
import re
import time
from pathlib import Path

import fitz  # PyMuPDF，切分大 PDF
import requests

import config
from stage1_layout import convert_layout_blocks

logger = logging.getLogger(__name__)

# API 单任务上限：≤1000 页、上传 ≤50MB。每片页数按 PDF 平均页体积自适应
# （片大小控制在 45MB 内），避免小页书被切成过多片白白排队
_MAX_JOB_PAGES = 1000
_MAX_JOB_BYTES = 45 * 1024 * 1024


def _chunk_pages(pdf_bytes: int, total_pages: int) -> int:
    """按 PDF 平均页体积计算每片页数（1..1000）。"""
    per_page = max(pdf_bytes / max(total_pages, 1), 1)
    return max(1, min(_MAX_JOB_PAGES, int(_MAX_JOB_BYTES / per_page)))


def _count_pages(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


class PaddleOcrProvider:
    """百度 PaddleOCR-VL（异步 job API）→ content_list.json + images/ + md。"""

    name = "paddleocr"

    def parse(self, pdf_path: str, work_dir: str, ocr: bool = True,
              progress=None, model: str | None = None) -> dict:
        if not config.PADDLEOCR_TOKEN:
            raise RuntimeError(
                "未配置 PADDLEOCR_TOKEN，无法提交 PaddleOCR 解析"
                "（在百度 AI Studio 申请后填入 .env 或设置面板）")

        pdf_path = Path(pdf_path)
        stem = pdf_path.stem
        out_dir = Path(work_dir) / "paddleocr"
        out_dir.mkdir(parents=True, exist_ok=True)
        images_out = out_dir / "images"
        images_out.mkdir(parents=True, exist_ok=True)

        model = model or config.PADDLEOCR_MODEL
        _report = progress or (lambda *a, **kw: None)

        total_pages = _count_pages(str(pdf_path))
        pdf_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        chunk_size = _chunk_pages(pdf_path.stat().st_size, total_pages)
        chunks_needed = (total_pages + chunk_size - 1) // chunk_size
        logger.info(f"Stage 1: PaddleOCR({model}) 解析 '{pdf_path.name}'")
        logger.info(f"  文件: {pdf_size_mb:.1f} MB, {total_pages} 页, "
                    f"分 {chunks_needed} 片 (每片 {chunk_size} 页)")

        headers = {"Authorization": f"bearer {config.PADDLEOCR_TOKEN}"}
        chunk_dir = out_dir / "_chunks"
        chunk_dir.mkdir(exist_ok=True)

        src = fitz.open(str(pdf_path))
        all_markdown = []
        all_blocks = []
        page_offset = 0
        try:
            for chunk_idx in range(chunks_needed):
                start_page = chunk_idx * chunk_size
                end_page = min(start_page + chunk_size, total_pages)

                _report(f"片 {chunk_idx + 1}/{chunks_needed}: "
                        f"第 {start_page + 1}-{end_page} 页 正在上传...",
                        chunk_idx / chunks_needed)
                logger.info(f"  片 {chunk_idx + 1}/{chunks_needed}: "
                            f"第 {start_page + 1}-{end_page} 页 ...")
                t0 = time.time()

                # 切出本片临时 PDF
                chunk_pdf = chunk_dir / f"_chunk_{chunk_idx}.pdf"
                sub = fitz.open()
                sub.insert_pdf(src, from_page=start_page, to_page=end_page - 1)
                sub.save(str(chunk_pdf))
                sub.close()

                # 提交 + 轮询 + 下载（重试 3 次）
                pages = None
                last_error = None
                for attempt in range(3):
                    try:
                        if attempt > 0:
                            _report(f"片 {chunk_idx + 1}/{chunks_needed}: "
                                    f"重试 {attempt + 1}/3...")
                        pages = self._run_job(chunk_pdf, headers, model, _report)
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < 2:
                            wait = (attempt + 1) * 10
                            logger.warning(
                                f"    尝试 {attempt + 1} 失败，{wait}s 后重试: {e}")
                            time.sleep(wait)
                try:
                    chunk_pdf.unlink()
                except OSError:
                    pass
                if pages is None:
                    logger.error(f"    片 {chunk_idx + 1} 重试 3 次后仍失败: "
                                 f"{last_error}")
                    raise last_error

                # 转换为 content_list 块（page_idx 加偏移）
                for local_idx, page in enumerate(pages):
                    all_markdown.append(page["markdown"]["text"])
                    for block in self._convert_page(
                            page, page_offset + local_idx, images_out):
                        _unicode_scripts_to_latex(block)
                        all_blocks.append(block)

                page_offset += (end_page - start_page)
                _report(f"片 {chunk_idx + 1}/{chunks_needed}: 完成 — "
                        f"{len(pages)} 页, 耗时 {time.time() - t0:.0f}s",
                        (chunk_idx + 1) / chunks_needed)
                logger.info(f"    完成: {len(pages)} 页, "
                            f"累计 {len(all_blocks)} blocks, "
                            f"耗时 {time.time() - t0:.0f}s")
        finally:
            src.close()
            try:
                chunk_dir.rmdir()
            except OSError:
                pass

        merged_md = "\n\n".join(all_markdown)
        (out_dir / f"{stem}.md").write_text(merged_md, encoding="utf-8")
        (out_dir / f"{stem}_content_list.json").write_text(
            json.dumps(all_blocks, ensure_ascii=False, indent=2),
            encoding="utf-8")

        logger.info(f"  合并完成: {len(merged_md):,} 字符, {len(all_blocks)} 个内容块")
        return {
            "markdown": merged_md,
            "content_list": all_blocks,
            "images_dir": str(images_out),
        }

    # ------------------------------------------------------------------
    # 单个 job：提交 → 轮询 → 下载 JSONL
    # ------------------------------------------------------------------
    def _run_job(self, chunk_pdf: Path, headers: dict, model: str,
                 _report) -> list[dict]:
        data = {
            "model": model,
            "optionalPayload": json.dumps({
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            }),
        }
        with open(chunk_pdf, "rb") as f:
            resp = requests.post(config.PADDLEOCR_API_URL, headers=headers,
                                 data=data, files={"file": f}, timeout=300)
        if resp.status_code != 200:
            raise RuntimeError(
                f"提交失败 HTTP {resp.status_code}: {resp.text[:300]}")
        job_id = resp.json()["data"]["jobId"]
        logger.info(f"    jobId: {job_id}")

        t0 = time.time()
        json_url = None
        while time.time() - t0 < config.PADDLEOCR_TIMEOUT:
            jr = requests.get(f"{config.PADDLEOCR_API_URL}/{job_id}",
                              headers=headers, timeout=60)
            jr.raise_for_status()
            d = jr.json()["data"]
            state = d["state"]
            if state == "done":
                json_url = d["resultUrl"]["jsonUrl"]
                break
            if state == "failed":
                raise RuntimeError(f"PaddleOCR 任务失败: {d.get('errorMsg')}")
            _report(f"解析中（{state}）...", None)
            time.sleep(5)
        if json_url is None:
            raise TimeoutError(
                f"PaddleOCR 任务超时（>{config.PADDLEOCR_TIMEOUT}s）")

        text = requests.get(json_url, timeout=300).text
        pages = []
        for line in text.strip().split("\n"):
            if line.strip():
                pages.extend(json.loads(line)["result"]["layoutParsingResults"])
        return pages

    # ------------------------------------------------------------------
    # 一页 → content_list 块
    # ------------------------------------------------------------------
    def _convert_page(self, page: dict, page_idx: int,
                      images_out: Path) -> list[dict]:
        """一页的解析结果 → content_list 块。

        块结构用 prunedResult.parsing_res_list（阅读顺序）；图片块
        block_content 为空，裁剪图 URL 在 markdown.images 里，键名内嵌
        bbox（img_in_image_box_x1_y1_x2_y2.jpg），按 bbox 精确匹配。
        bbox 由像素坐标按 prunedResult 的 width/height 换算为 0-1000 千分位。
        """
        pruned = page.get("prunedResult") or {}
        page_w = pruned.get("width") or 0
        page_h = pruned.get("height") or 0
        image_keys = page["markdown"].get("images") or {}

        def find_key(bbox) -> str | None:
            pat = f"box_{bbox[0]}_{bbox[1]}_{bbox[2]}_{bbox[3]}"
            for k in image_keys:
                if pat in k:
                    return k
            return None

        raws = []
        for b in pruned.get("parsing_res_list", []):
            label = b.get("block_label") or ""
            px_bbox = b.get("block_bbox")
            img_name = ""
            img_url = None
            if label in ("chart", "image", "table_image"):
                key = find_key(px_bbox or [0, 0, 0, 0])
                if key:
                    img_name = f"p{page_idx:03d}_{Path(key).name}"
                    img_url = image_keys[key]
            raws.append({
                "label": label,
                "content": b.get("block_content") or "",
                "index": b.get("block_id", 0),
                "bbox": _norm_bbox(px_bbox, page_w, page_h),
                "_img_name": img_name,
                "_url": img_url,
            })
        return convert_layout_blocks(raws, page_idx, images_out,
                                     get_image_url=self._image_url)

    @staticmethod
    def _image_url(raw: dict) -> str | None:
        return raw.get("_url")


def _norm_bbox(px_bbox, page_w: int, page_h: int) -> list[int] | None:
    """像素 xyxy → 0-1000 千分位 xyxy（越界截断）；缺尺寸或非法返回 None。"""
    if not isinstance(px_bbox, (list, tuple)) or len(px_bbox) != 4:
        return None
    if not page_w or not page_h:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in px_bbox)
        vals = [x1 / page_w * 1000, y1 / page_h * 1000,
                x2 / page_w * 1000, y2 / page_h * 1000]
    except (TypeError, ValueError):
        return None
    vals = [min(max(round(v), 0), 1000) for v in vals]
    if vals[2] <= vals[0] or vals[3] <= vals[1]:
        return None
    return vals


# ------------------------------------------------------------------
# PaddleOCR 专属：Unicode 上下标 → LaTeX（逐字移植自 Papers_Converter）
#
# PaddleOCR-VL 在正文里用 Unicode 上下标（LiCoO₂、Li⁺/Na⁺、H₂O）而非
# 契约要求的 $...$（表格里它反而输出 LaTeX）。逐字 run 转换：
# "LiCoO₂" → "LiCoO$_{2}$"，"Li⁺/Na⁺" → "Li$^{+}$/Na$^{+}$"。
# 已有 $...$ 区段不动。
# ------------------------------------------------------------------
_SUB_MAP = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
                    "0123456789+-=()aehijklmnoprstuvx"))
_SUP_MAP = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ",
                    "0123456789+-=()ni"))
_SUB_RUN_RE = re.compile("[" + "".join(_SUB_MAP) + "]+")
_SUP_RUN_RE = re.compile("[" + "".join(_SUP_MAP) + "]+")
_MATH_SPAN_SPLIT_RE = re.compile(r"(\$[^$]*\$)")
# Unicode 无下标点：下标小数被拆成 "$_{0}$.$_{25}$" → 合并为 "$_{0.25}$"
_SCRIPT_DOT_MERGE_RE = re.compile(r"\$([_^])\{([^{}]*)\}\$\.\$([_^])\{([^{}]*)\}\$")
# PaddleOCR 把正文标点做 markdown 转义（"1\. 要件"、"\[德\]"），数学区外要
# 解掉，否则形状检测（^\d+\.）失效、EPUB 里残留反斜杠
_MD_ESCAPE_RE = re.compile(r"\\([.\[\]\-*#_()>+!|~])")


def _convert_scripts(text: str) -> str:
    text = _SUB_RUN_RE.sub(
        lambda m: "$_{" + "".join(_SUB_MAP[c] for c in m.group(0)) + "}$", text)
    text = _SUP_RUN_RE.sub(
        lambda m: "$^{" + "".join(_SUP_MAP[c] for c in m.group(0)) + "}$", text)
    while True:
        merged = _SCRIPT_DOT_MERGE_RE.sub(
            lambda m: (f"${m.group(1)}{{{m.group(2)}.{m.group(4)}}}$"
                       if m.group(1) == m.group(3) else m.group(0)), text)
        if merged == text:
            return text
        text = merged


def _convert_scripts_outside_math(s: str) -> str:
    """按 $...$ 分段（捕获组保留定界段），只转换数学区外的部分。"""
    parts = _MATH_SPAN_SPLIT_RE.split(s)  # 偶数索引 = 数学区外
    for i in range(0, len(parts), 2):
        parts[i] = _MD_ESCAPE_RE.sub(r"\1", parts[i])
        parts[i] = _convert_scripts(parts[i])
    text = "".join(parts)
    # 源文本里上下标前的空格是 OCR 噪声（"LiCoO ₂"），转换后贴回主体
    return re.sub(r"(?<=[A-Za-z0-9]) (\$[_^]\{)", r"\1", text)


def _unicode_scripts_to_latex(block: dict) -> None:
    """就地转换块的文本字段（text/table_body/图注/表注）。"""
    for key in ("text", "table_body"):
        s = block.get(key)
        if s:
            block[key] = _convert_scripts_outside_math(s)
    for key in ("image_caption", "table_caption"):
        caps = block.get(key)
        if caps:
            block[key] = [_convert_scripts_outside_math(c) for c in caps]
