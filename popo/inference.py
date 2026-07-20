# vendor 自 MinerU-Popo (https://github.com/opendatalab/MinerU-Popo, MIT License)
# 原始路径: post_processing/inference.py（含 post_processing/run_inference.py 的校验部分）
# 本地修改:
#   - 去掉 sys.path 黑科技，改为包内相对导入；
#   - 仅保留共享工具：输入校验 / 候选筛选 / prompt 构建 / 输出解析 / 动态分块，
#     供 hybrid 引擎复用；本地 VLM 推理驱动（run_inference、页面拼图渲染、
#     原始记录落盘）已随 Popo 引擎一并移除。
"""
结构分析共享工具集。

- validate_pages: pages dict 输入校验（类型/bbox 范围）
- filter_contd / filter_title / filter_image / filter_table_merge: 候选筛选
- add_contd / add_title / add_image / add_table_merge: prompt 构建
- extract_label1 / extract_label2 / parse_string_*: 模型输出解析
- adaptive_chunk: 动态分块（重叠边界 + token 均衡）
"""

import logging
import math
import re

from bs4 import BeautifulSoup

from .table_merge_filter import filter_table_merge_candidates
from .table_utils import (
    detect_table_headers,
    get_visual_last_row_cells_content_with_span_info,
    get_table_first_data_row_cells_with_span_info,
    extract_last_coordinates,
)

logger = logging.getLogger(__name__)


# ============================================================
# 输入校验（vendor 自 post_processing/run_inference.py）
# ============================================================
ALLOWED_BLOCK_TYPES = {
    "title",
    "text",
    "list_item",
    "equation",
    "image",
    "table",
    "image_caption",
    "table_caption",
    "image_footnote",
    "table_footnote",
    "page_title",
    "page_number",
    "page_footnote",
    "header",
    "aside_text",
    "footer",
}


def _validate_bbox(doc_key: str, page: str, block_index: int, bbox, strict: bool) -> None:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"{doc_key} page={page} block={block_index}: bbox must be a 4-item list")
    values = []
    for value in bbox:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{doc_key} page={page} block={block_index}: non-numeric bbox={bbox}") from exc
        if not math.isfinite(number):
            raise ValueError(f"{doc_key} page={page} block={block_index}: non-finite bbox={bbox}")
        values.append(number)
    x1, y1, x2, y2 = values
    if x2 < x1 or y2 < y1:
        raise ValueError(f"{doc_key} page={page} block={block_index}: invalid xyxy order bbox={bbox}")
    if strict and (min(values) < -1e-6 or max(values) > 1.000001):
        raise ValueError(
            f"{doc_key} page={page} block={block_index}: bbox is not xyxy_01 bbox={bbox}; "
            "请先用 convert.content_list_to_pages 做归一化"
        )


def validate_pages(pages: dict, doc_key: str = "doc", strict_bbox: bool = True) -> int:
    """校验 pages 结构与每个 block 的 type/bbox，返回 block 总数。"""
    if not isinstance(pages, dict):
        raise ValueError(f"{doc_key}: expected pages dict, got {type(pages).__name__}")
    block_count = 0
    for page, blocks in pages.items():
        if not isinstance(blocks, list):
            raise ValueError(f"{doc_key} page={page}: expected block list")
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise ValueError(f"{doc_key} page={page} block={block_index}: expected block dict")
            block_type = str(block.get("type", ""))
            if block_type not in ALLOWED_BLOCK_TYPES:
                raise ValueError(
                    f"{doc_key} page={page} block={block_index}: unexpected type={block_type!r}"
                )
            _validate_bbox(doc_key, str(page), block_index, block.get("bbox"), strict_bbox)
            block_count += 1
    return block_count


# ============================================================
# PDF 页面拼图（渲染喂给 VLM）
# ============================================================
# ============================================================
# 候选筛选与启发式规则
# ============================================================
termination_chars = {
    ".",
    "。",
    "?",
    "!",
    "？",
    "！",
    "¿",
    "¡",
    "؟",
    "ฯ",
    "۔",
    ":",
    "：",
    "……",
    ";",
    "；"
}

close_chars = {
    "’",
    "”",
    "'",
    "\"",
    "）",
    "」",
    "】",
    "]",
    ")",
}

termination_pattern = "[" + re.escape("".join(termination_chars)) + "]"


def get_tail_sentence(text):
    """Get the last sentence"""
    if not text.strip():
        return text.strip()
    parts = re.split(termination_pattern, text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts[-1] if parts else text.strip()


def get_head_sentence(text):
    """Get the first sentence"""
    if not text.strip():
        return text.strip()
    match = re.search(termination_pattern, text)
    if match:
        end_index = match.end()
        return text[:end_index].strip()
    else:
        return text.strip()


def is_list_item(s):
    """Judge if the text is a preifx of a list item"""

    pattern = r'''
        ^
        (?:
            # 1. 2. or 1) 2)
            \d+[\.\)] |
            \d+） |
            \d+．|
            \d+、|
            # (1) (2)
            \(\d+\) |
            \([a-z]\) |
            \([A-Z]\) |
            （\d+） |
            # A. B. or A) B)
            [A-Z][\.\)] |
            # a. b. or a) b)
            [a-z][\.\)] |
            # CN number（一、 二、）
            [一二三四五六七八九十百千万]+、 |
            \([一二三四五六七八九十百千万]+\) |
            （一二三四五六七八九十百千万]+） |
            # ① ② ③
            [①-⑳㉑-㉟] |
            # • ▪ ▫
            [•▪▫] |
            # Ⅰ. Ⅱ. Ⅲ.
            [IVXLCDM]+\. |
            - |
            \$ |
            \\t |
            [\[\(「【（] |
            # CN section
            第[一二三四五六七八九十百千万][条节章]
        )
        \s*
    '''

    return re.match(pattern, s, re.IGNORECASE | re.VERBOSE) is not None


def merge_rules(str1, str2):
    """Heuristics of text termination, prefix and length"""
    if not str1 or not str2:
        return False

    str1_trimmed = str1.strip()
    if str1_trimmed and str1_trimmed[-1] in termination_chars:
        return False
    if len(str1_trimmed) > 1 and str1_trimmed[-1] in close_chars and str1_trimmed[-2] in termination_chars:
        return False

    if len(str1) < 10:
        return False

    if is_list_item(str2):
        return False

    if "\t" in str1 or "\t" in str2:
        return False

    if str1[0].isdigit() and str2[0].isdigit():
        return False

    return True


def filter_contd(blocks):
    """Filter input for Text Truncation Analysis"""
    potential_idx = []
    judge_blocks = []
    valid_pairs = {}
    for i, block in enumerate(blocks):
        if block["type"] in ["text", "list_item"]:
            for pos in range(i+1, len(blocks)):
                if 'equation' in blocks[pos]['type'] or 'title' in blocks[pos]['type']:
                    break
                if blocks[pos]['type'] in ["text", "list_item"] and merge_rules(block["content"], blocks[pos]['content']):
                    if i not in potential_idx:
                        potential_idx.append(i)
                    if pos not in potential_idx:
                        potential_idx.append(pos)
                    valid_pairs[i] = pos
                    break

    for i in potential_idx:
        text = blocks[i]['content']
        bbox = blocks[i]['bbox']
        head = get_head_sentence(text)
        tail = get_tail_sentence(text)
        text_short = head if head == tail else head + ' ... ' + tail
        text_short = text_short if len(text_short) <= 103 else text_short[:50] + '...' + text_short[-50:]
        judge_blocks.append({'idx': i, 'content': text_short, 'page': blocks[i]['page'], 'bbox': [str(int(bbox[1]*1000)), str(int(bbox[0]*1000)), str(int(bbox[3]*1000)), str(int(bbox[2]*1000))]})

    return judge_blocks


def filter_title(blocks, tol=0.05):
    """Filter input for Title Hierarchy Analysis"""
    judge_blocks = []
    for i, block in enumerate(blocks):
        bbox = blocks[i]['bbox']
        if block["type"] in ["title", "TOC-title", "section-title"]:
            judge_blocks.append({'idx': i, 'content': blocks[i]['content'][:50], 'page': blocks[i]['page'], 'bbox': [str(int(bbox[1]*1000)), str(int(bbox[0]*1000)), str(int(bbox[3]*1000)), str(int(bbox[2]*1000))]})

    return judge_blocks


def check_overlap(visual_blocks, large_blocks):
    judge_blocks = []
    large_block_linking = {}
    for i, block in visual_blocks.items():
        flag = True
        bbox = block["bbox"]
        for j, lblock in large_blocks.items():
            lbbox = lblock["bbox"]
            if (block["type"] in ['image', 'chart', 'image_footnote', 'image_caption', 'figure', 'fig-title', 'fig-caption']) and (bbox[0] >= 0.95*lbbox[0] and bbox[1] >= 0.95*lbbox[1] and bbox[2] <= 1.05*lbbox[2] and bbox[3] <= 1.05*lbbox[3]):
                large_block_linking[i] = j
                flag = False
                break
        if flag:
            typ = block['type']
            if typ in ['image_block', 'chart', 'figure']:
                typ = 'image'
            elif typ in ["title", "TOC-title", "section-title"]:
                typ = 'title'
            elif typ in ['fig-title', 'fig-caption']:
                typ = 'image_caption'
            elif typ in ['tab-title', 'tab-caption']:
                typ = 'table_caption'
            content = "None" if typ in ['image', 'table'] else block['content']
            judge_blocks.append({'idx': i, 'type': typ, 'content': content[:50], 'page': block['page'], 'bbox': [str(int(bbox[1]*1000)), str(int(bbox[0]*1000)), str(int(bbox[3]*1000)), str(int(bbox[2]*1000))]})
    return judge_blocks, large_block_linking


def filter_image(blocks, tol=0.05):
    """Filter input for Text-Image Association Analysis"""
    visual_blocks = {}
    large_blocks = {}
    for i, block in enumerate(blocks):
        if block["type"] == 'seal':
            block["type"] = 'image'
        if block["type"] in ['image_block', 'image', 'table', 'chart', 'table_footnote', 'image_footnote', 'table_caption', 'image_caption', "title", "TOC-title", "section-title", 'figure', 'fig-title', 'fig-caption', 'tab-title', 'tab-caption']:
            visual_blocks[i] = block
        if block["type"] in ['image_block']:
            large_blocks[i] = block

    judge_blocks, exist_linking = check_overlap(visual_blocks, large_blocks)

    return judge_blocks, exist_linking


def filter_table_merge(blocks):
    """Filter input for Table Merge Detection.

    Finds adjacent-page table pairs that pass pre-LLM screening,
    and prepares the row data needed for the merge prompt.

    Returns:
        list of dicts: [{"table1_idx": int, "table2_idx": int,
                          "upper_row_ss": list, "lower_row_ss": list}]
    """
    tables_by_page = {}
    for i, block in enumerate(blocks):
        if block["type"] == "table":
            page = block["page"]
            if page not in tables_by_page:
                tables_by_page[page] = []
            tables_by_page[page].append(i)

    if len(tables_by_page) < 2:
        return []

    merge_inputs = []
    sorted_pages = sorted(tables_by_page.keys())

    for p_idx in range(len(sorted_pages) - 1):
        page1 = sorted_pages[p_idx]
        page2 = sorted_pages[p_idx + 1]
        if page2 != page1 + 1:
            continue

        table1_idx = tables_by_page[page1][-1]
        table2_idx = tables_by_page[page2][0]

        can_merge, reason = filter_table_merge_candidates(blocks, table1_idx, table2_idx)
        if not can_merge:
            continue

        try:
            soup1 = BeautifulSoup(blocks[table1_idx].get("content", ""), "html.parser")
            soup2 = BeautifulSoup(blocks[table2_idx].get("content", ""), "html.parser")
        except Exception:
            continue

        if not soup1.find_all("tr") or not soup2.find_all("tr"):
            continue

        header_rows, _, _ = detect_table_headers(soup1, soup2)
        upper_row_ss = get_visual_last_row_cells_content_with_span_info(soup1)
        lower_row_ss = get_table_first_data_row_cells_with_span_info(soup2, header_rows)
        merge_inputs.append({
            "table1_idx": table1_idx,
            "table2_idx": table2_idx,
            "upper_row_ss": upper_row_ss,
            "lower_row_ss": lower_row_ss,
        })

    return merge_inputs


# ============================================================
# prompt 构建
# ============================================================
def add_contd(judge_blocks):
    input_list = []
    for block in judge_blocks:
        input_list.append(f"<|id|>{block['idx']}<|page|>{block['page']}<|box|>{' '.join(block['bbox'])}<|content|>{block['content']}")
    input_txt = '\n'.join(input_list)

    return f"<image>\nTruncation Detection: {input_txt}"


def add_title(judge_blocks):
    input_list = []
    for block in judge_blocks:
        input_list.append(f"<|id|>{block['idx']}<|page|>{block['page']}<|box|>{' '.join(block['bbox'])}<|content|>{block['content']}")
    input_txt = '\n'.join(input_list)

    return f"<image>\nTitle Level Analysis: {input_txt}"


def add_image(judge_blocks):
    input_list = []
    for block in judge_blocks:
        input_list.append(f"<|id|>{block['idx']}<|type|>{block['type']}<|page|>{block['page']}<|box|>{' '.join(block['bbox'])}<|content|>{block['content']}")
    input_txt = '\n'.join(input_list)

    return f"<image>\nImage-Text Correlation Analysis: {input_txt}"


def add_table_merge(upper_row_ss, lower_row_ss):
    prompt = f"""
## Table 1 (Previous Page - Last Table)

**Caption:** :""
**Last Row(s) Data:**
{upper_row_ss}

---

## Table 2 (Current Page - First Table)

**Caption:** :""
**First Data Row(s):**
{lower_row_ss}
"""
    return prompt


# ============================================================
# 模型输出解析
# ============================================================
def extract_label1(s):
    result = []
    for line in s.strip().split('\n'):
        try:
            if not line:
                continue
            src_part, tgt_part = line.split("<|tgt_id|>")
            src_id = src_part.split("<|src_id|>")[1]
            tgt_id = tgt_part
            result.append({"src_id": int(src_id), "tgt_id": int(tgt_id)})
        except Exception:
            continue
    return result


def extract_label2(s):
    result = []
    for line in s.strip().split('\n'):
        try:
            if not line:
                continue
            id_part, level_part = line.split("<|level|>")
            idx = id_part.split("<|id|>")[1]
            level = level_part
            if int(level) >= 0:
                result.append({"id": int(idx), "level": int(level)})
        except Exception:
            continue
    return result


def parse_string_notype(input_string):
    prefix1 = "<image>\nTruncation Detection: "
    prefix2 = "<image>\nTitle Level Analysis: "
    if input_string.startswith(prefix1):
        content = input_string[len(prefix1):]
    elif input_string.startswith(prefix2):
        content = input_string[len(prefix2):]
    else:
        content = input_string

    content = '\n' + content
    parts = content.split("\n<|id|>")

    if parts and parts[0] == "":
        parts = parts[1:]

    results = []

    for part in parts:
        if not part.strip():
            continue

        page_split = part.split("<|page|>")
        id_value = page_split[0].strip()

        box_split = page_split[1].split("<|box|>")
        page_value = box_split[0].strip()

        content_split = box_split[1].split("<|content|>")
        box_value = content_split[0].strip()
        content_value = content_split[1].strip() if len(content_split) > 1 else ""

        item = {
            'id': id_value,
            'page': int(page_value),
            'box': box_value,
            'content': content_value
        }
        results.append(item)
    return results


def parse_string_type(input_string):
    prefix = "<image>\nImage-Text Correlation Analysis: "
    if input_string.startswith(prefix):
        content = input_string[len(prefix):]
    else:
        content = input_string

    content = '\n' + content
    parts = content.split("\n<|id|>")
    if parts and parts[0] == "":
        parts = parts[1:]

    results = []

    for part in parts:
        if not part.strip():
            continue

        type_split = part.split("<|type|>")
        id_value = type_split[0].strip()

        page_split = type_split[1].split("<|page|>")
        type_value = page_split[0].strip()

        box_split = page_split[1].split("<|box|>")
        page_value = box_split[0].strip()

        content_split = box_split[1].split("<|content|>")
        box_value = content_split[0].strip()
        content_value = content_split[1].strip() if len(content_split) > 1 else ""

        item = {
            'id': id_value,
            'type': type_value,
            'page': int(page_value),
            'box': box_value,
            'content': content_value
        }
        results.append(item)
    return results


# ============================================================
# 动态分块
# ============================================================
def adaptive_chunk(items, chunk_size=12, overlap=1):
    if not items:
        return [], []

    sorted_items = sorted(items, key=lambda x: x['page'])
    pages = [item['page'] for item in sorted_items]
    unique_pages = sorted(set(pages))

    boundaries = []
    current_min = unique_pages[0]

    while current_min < unique_pages[-1]:
        target = current_min + chunk_size

        # Search from pages in a range
        search_range = range(max(unique_pages[0], target-5),
                           min(unique_pages[-1], target+5)+1)

        # Count the frequency of the target type of item in the page
        freq = {}
        for page in search_range:
            if page in unique_pages:
                freq[page] = pages.count(page)

        if freq:
            boundary = max(freq, key=freq.get)
        else:
            boundary = min((x for x in unique_pages if x > target), default=unique_pages[-1])

        boundaries.append(boundary)
        current_min = boundary

    # Chunking
    ranges = []
    chunks = []
    prev_boundary = unique_pages[0]

    for boundary in boundaries:
        chunk_items = [item for item in sorted_items
                      if prev_boundary - overlap <= item['page'] <= boundary + overlap]
        if chunk_items:
            chunks.append(chunk_items)
            start = prev_boundary if prev_boundary == unique_pages[0] else prev_boundary - overlap
            end = min(unique_pages[-1], boundary + overlap)
            ranges.append([start, end])
        prev_boundary = boundary

    # The last chunk
    last_chunk = [item for item in sorted_items
                 if prev_boundary - overlap <= item['page'] <= unique_pages[-1]]
    if last_chunk:

        start = prev_boundary if prev_boundary == unique_pages[0] else prev_boundary - overlap
        if unique_pages[-1] - start > 2:
            chunks.append(last_chunk)
            ranges.append([start, unique_pages[-1]])

    return ranges, chunks
