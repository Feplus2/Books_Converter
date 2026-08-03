"""
MinerU 云 API content_list → Popo pages 格式转换（全新代码，非 vendor）。

把 Stage1 输出的 content_list.json（扁平 block 列表，bbox 为 0-1000 千分位
xyxy，page_idx 从 0 起）转成 Popo 推理所需的按页分组结构：
    {"1": [block, ...], "2": [...]}    # 页码 1 起 = page_idx + 1

每个输出 block：
    {
        "type": popo 类型,
        "content": str | None,
        "bbox": [x1, y1, x2, y2],       # 0..1 浮点 xyxy
        "source_label": 原始 MinerU type,
        "source_id": f"{doc_id}:{content_list 索引}",
        # title 另带 "title_level"（若原 block 有 text_level）
    }
"""

import logging
import re

logger = logging.getLogger(__name__)

# 直接丢弃的 MinerU block 类型
_SKIP_TYPES = {"header", "footer", "page_number", "aside_text", "discarded", "chart"}

# 编/篇/卷/部 形状的分隔页文本（常被 MinerU 误标为 header，需要捞回）
_PART_HEADER = re.compile(
    r"^第[一二三四五六七八九十百零〇0-9]+[编篇卷部]"
    r"|^(part|volume|book|teil|partie|tome)\b",
    re.I,
)


def _norm_bbox(bbox) -> list[float] | None:
    """千分位 xyxy → 0..1 浮点 xyxy（越界截断）；非法返回 None。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        vals = [float(v) / 1000.0 for v in bbox]
    except (TypeError, ValueError):
        return None
    vals = [min(max(v, 0.0), 1.0) for v in vals]
    if vals[2] < vals[0] or vals[3] < vals[1]:
        return None
    return vals


def content_list_to_pages(content_list: list[dict], doc_id: str = "doc") -> dict[str, list[dict]]:
    """
    把 MinerU content_list 转成 Popo 的 pages dict。

    Args:
        content_list: Stage1 的 *_content_list.json 加载后的 block 列表。
        doc_id: 文档标识，用于生成 source_id。

    Returns:
        {"1": [block, ...], ...}，页码 1 起，按页码升序。
    """
    page_map: dict[int, list[dict]] = {}
    n_skipped = 0
    seen_part_headers: set[str] = set()

    for idx, block in enumerate(content_list):
        bbox = _norm_bbox(block.get("bbox"))
        page_idx = block.get("page_idx")
        if bbox is None or page_idx is None:
            n_skipped += 1
            continue

        page_no = int(page_idx) + 1
        src_type = str(block.get("type", ""))
        source_id = f"{doc_id}:{idx}"

        def emit(btype, content, bbox_vals=None, sid=None, title_level=None):
            # image/table 允许空 content，其余类型空 content 跳过
            if btype not in ("image", "table"):
                if not isinstance(content, str) or not content.strip():
                    return
            out = {
                "type": btype,
                "content": content.strip() if isinstance(content, str) else content,
                "bbox": list(bbox_vals) if bbox_vals is not None else list(bbox),
                "source_label": src_type,
                "source_id": sid if sid is not None else source_id,
            }
            if title_level is not None:
                out["title_level"] = title_level
            page_map.setdefault(page_no, []).append(out)

        if src_type == "text":
            text_level = block.get("text_level")
            if text_level is not None and int(text_level) >= 1:
                emit("title", block.get("text"), title_level=int(text_level))
            else:
                emit("text", block.get("text"))

        elif src_type == "title":
            text_level = block.get("text_level")
            emit("title", block.get("text"),
                 title_level=int(text_level) if text_level is not None else None)

        elif src_type == "image":
            emit("image", None)
            captions = block.get("image_caption")
            if isinstance(captions, list):
                for n, cap in enumerate(captions):
                    cap_text = str(cap).strip()
                    if not cap_text:
                        continue
                    # bbox 沿用 image，y1 微调到 y2 处表示在图下方
                    cap_bbox = [bbox[0], bbox[3], bbox[2], bbox[3]]
                    emit("image_caption", cap_text, bbox_vals=cap_bbox,
                         sid=f"{source_id}.cap{n}")

        elif src_type == "table":
            captions = block.get("table_caption")
            if isinstance(captions, list):
                for n, cap in enumerate(captions):
                    cap_text = str(cap).strip()
                    if not cap_text:
                        continue
                    # bbox 沿用 table，y 调到表格上方，放在 table block 之前
                    cap_bbox = [bbox[0], bbox[1], bbox[2], bbox[1]]
                    emit("table_caption", cap_text, bbox_vals=cap_bbox,
                         sid=f"{source_id}.cap{n}")
            emit("table", block.get("table_body"))

        elif src_type in ("equation", "interline_equation"):
            emit("equation", block.get("text"))

        elif src_type == "code":
            emit("text", block.get("code_body") or block.get("text"))

        elif src_type == "list":
            items = block.get("list_items")
            if isinstance(items, list):
                emit("text", "\n".join(str(it) for it in items))
            else:
                emit("text", block.get("text"))

        elif src_type == "page_footnote":
            # 页脚注：保留，由 stage3 做锚点匹配并渲染为章末尾注
            emit("page_footnote", block.get("text"))

        elif src_type in _SKIP_TYPES:
            # 页眉的每种文本只保留首次出现（防每页运行的页眉重复）：
            # - 编/篇/卷/部 形状：常被引擎误标为 header，直接捞回为标题
            # - 其余页眉：首次出现保留为 text（它可能其实是节起始页的标题，
            #   如'参考文献'的章节首页页眉，交给 stage2 锚点晋升裁决）
            if src_type == "header":
                htext = (block.get("text") or "").strip()
                if htext:
                    key = re.sub(r"\s+", "", htext)
                    if key not in seen_part_headers:
                        seen_part_headers.add(key)
                        if _PART_HEADER.match(htext):
                            emit("title", htext)
                        else:
                            emit("text", htext)
                        continue
            n_skipped += 1
            continue

        else:
            # 未知类型按正文处理
            emit("text", block.get("text"))

    if n_skipped:
        logger.info(f"content_list 转换: 跳过 {n_skipped} 个 block（无 bbox 或被丢弃类型）")

    return {str(p): page_map[p] for p in sorted(page_map)}
