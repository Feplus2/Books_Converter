"""Stage 1 共享：layout 块列表 → content_list 契约的转换逻辑。

移植自 Papers_Converter 同名模块（PP-DocLayout 风格标签体系，PaddleOCR-VL
等 layout 系引擎共用），并按 Books_Converter 契约做了三点改造：

- 每块携带 bbox（0-1000 千分位 xyxy，由 provider 归一化后随 raw 传入）——
  本管线 popo/convert.py 会丢弃无 bbox 的块，bbox 是契约必需字段；
- 块类型收敛到本管线已知集合（text/image/table/equation/page_footnote/
  header/footer/page_number/aside_text），无 ref_text 等新类型；
- 新增 vision_footnote 标签（PaddleOCR-VL-1.6 实测出现，图下说明文字），
  按图注候选处理。

Provider 只需把引擎原始块归一化为 {"label", "content", "index", "bbox", ...}
并提供图片下载回调。踩坑记录见 Papers_Converter docs/ocr-providers.md。
"""

import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 标签映射
# ------------------------------------------------------------------
# 出版噪声（页眉/页脚/页码/报头图/旁注）：保留文本（stage2 元数据提取要读）
_NOISE_MAP = {
    "header": "header",
    "footer": "footer",
    "number": "page_number",
    "page_number": "page_number",
    "aside_text": "aside_text",
    "header_image": "header",   # 报头图，content 多为 URL，文本置空（见 convert）
}
_FOOTNOTE_LABELS = {"footnote", "page_footnote"}
_FORMULA_LABELS = {"formula", "equation", "interline_formula", "display_formula"}
_TEXT_LABELS = {"text", "abstract", "doc_title", "paragraph_title"}
_HEADING_LABELS = {"doc_title", "paragraph_title"}
_IMAGE_LABELS = {"image", "chart", "table_image", "seal", "header_image"}
# vision_footnote：PaddleOCR-VL-1.6 的图下说明文字，按图注候选走绑定流程
_CAPTION_LABELS = {"figure_title", "table_title", "vision_footnote"}

# panel 字母标："A"、"(b)"、"（c）" 等，图注名下的噪声
_PANEL_LABEL_RE = re.compile(r"^[（(]?[A-Za-z0-9]{1,2}[)）]?$")
# 带编号的正式图注起点（"Figure 1." / "Fig. 12.4" / "FIGURE 3:" / "图 1"）
_FIG_CAPTION_RE = re.compile(r"^(Fig(?:ure|\.)?\s*\d|图\s*\d)", re.IGNORECASE)
# 带编号的正式表注起点（"Table 1" / "表 1"）
_TABLE_CAPTION_RE = re.compile(r"^(Table\s*\d|表\s*\d)", re.IGNORECASE)

# ------------------------------------------------------------------
# 文本清洗
# ------------------------------------------------------------------
_DIV_RE = re.compile(r"</?div[^>]*>")
_HEADING_MARK_RE = re.compile(r"^#{1,6}\s+")
# \mathrm{...} 等 LaTeX 文本命令（GLM 系把内容打成字母间带空格的形态）
_MATHRM_RE = re.compile(r"\\(?:mathrm|text|operatorname)\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
# 上下标组（GLM 会把 "_{0.25}" 打成 "_{0. 2 5}"）
_SCRIPT_GROUP_RE = re.compile(r"(?<=[_^])\{([^{}]*)\}")
# \mathrm 内部：连续单字母/数字 token（"C D 3 4" → "CD34"），多字母单词不受影响
_SPACED_CHARS_RE = re.compile(r"\b(?:[A-Za-z0-9] )+[A-Za-z0-9]\b")
# 上下标组内部：只合并数字 run（"2 5" → "25"）与小数点空格（"0. 25" → "0.25"），
# 字母下标（_{i j} 可能是合法多指标）不动
_SPACED_DIGITS_RE = re.compile(r"\b(?:\d )+\d\b")
_DECIMAL_SPACE_RE = re.compile(r"(?<=\d\.) (?=\d)")


def _collapse_spaced(s: str) -> str:
    return _SPACED_CHARS_RE.sub(lambda r: r.group(0).replace(" ", ""), s)


def normalize_math(text: str) -> str:
    """修正 GLM 系模型的公式伪影：

    1. 定界符带空格：" $ ^{1-3} $ " → "$^{1-3}$"（双 padding 恒可收；单侧仅
       在紧邻明确数学起始符时收，避免误伤 "$100"）。
    2. \\mathrm{...} 内字母被空格拆开："\\mathrm{C D 3 4^{+}}" → "\\mathrm{CD34^{+}}"。
    3. 上下标组内数字被空格拆开："_{0. 2 5}" → "_{0.25}"。
    """
    # 起始 $ 前不能紧跟 } / 字母 / 数字 / ]（排除上一个公式收尾 $ 被误当起始）
    text = re.sub(r"(?<![A-Za-z0-9})\]])\$ (\S(?:[^$]*\S)?) \$", r"$\1$", text)
    text = re.sub(r"\$ (?=[\^\\_({])", "$", text)

    def _collapse(m: re.Match) -> str:
        inner = _collapse_spaced(m.group(1))
        return m.group(0).replace(m.group(1), inner)
    text = _MATHRM_RE.sub(_collapse, text)

    def _collapse_digits(m: re.Match) -> str:
        inner = _SPACED_DIGITS_RE.sub(lambda r: r.group(0).replace(" ", ""), m.group(1))
        inner = _DECIMAL_SPACE_RE.sub("", inner)
        return m.group(0).replace(m.group(1), inner)
    return _SCRIPT_GROUP_RE.sub(_collapse_digits, text)


def clean_text(content: str) -> str:
    """剥离 HTML 包裹与 markdown 标题标记，规范化公式，清掉字面 \\n
    （PaddleOCR 表格单元格内残留的字面换行符）。"""
    text = _DIV_RE.sub("", content or "")
    return normalize_math(text).replace("\\n", " ").strip()


def is_url(content: str) -> bool:
    return (content or "").startswith(("http://", "https://"))


def download_image(url: str, name: str, images_out: Path) -> str | None:
    """下载裁剪图 URL 到本地，成功返回文件名，失败返回 None。"""
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        (images_out / name).write_bytes(resp.content)
        return name
    except Exception as e:
        logger.warning(f"    裁剪图下载失败 {name}: {e}")
        return None


# ------------------------------------------------------------------
# 主转换
# ------------------------------------------------------------------

def convert_layout_blocks(raws: list[dict], page_idx: int, images_out: Path,
                          get_image_url) -> list[dict]:
    """一页的归一化块 → content_list 块（含图注绑定）。

    Args:
        raws: [{"label": str, "content": str, "index": int, "bbox": list|None}]
            按阅读顺序；bbox 为 0-1000 千分位 xyxy（契约必需，由 provider 归一化）
        page_idx: 0 基页码
        images_out: 图片落盘目录
        get_image_url: callable(raw) -> str|None，返回该块的裁剪图 URL
            （仅图片类块会调用；返回 None 视为无图）
    """
    items = []
    for raw in raws:
        block = _convert_one(raw, page_idx, images_out, get_image_url)
        if block is not None:
            items.append(block)
    return _bind_figure_captions(items)


def _convert_one(raw: dict, page_idx: int, images_out: Path,
                 get_image_url) -> dict | None:
    label = raw.get("label") or ""
    content = raw.get("content") or ""
    bbox = raw.get("bbox")

    def _base(btype: str) -> dict:
        b = {"type": btype, "page_idx": page_idx}
        if bbox is not None:
            b["bbox"] = list(bbox)
        return b

    # 出版噪声
    if label in _NOISE_MAP:
        b = _base(_NOISE_MAP[label])
        b["text"] = "" if label == "header_image" else clean_text(content)
        return b
    if label in _FOOTNOTE_LABELS:
        b = _base("page_footnote")
        b["text"] = clean_text(content)
        return b

    # 图片类：URL 由 provider 解析（PaddleOCR 按 bbox 找 markdown.images 键）
    if label in _IMAGE_LABELS or is_url(content):
        url = get_image_url(raw)
        if not url:
            return None
        name = download_image(url, raw["_img_name"], images_out)
        if name is None:
            return None
        b = _base("image")
        b["img_path"] = name
        b["image_caption"] = []
        return b

    # 公式类
    if label in _FORMULA_LABELS:
        text = clean_text(content)
        if not text:
            return None
        b = _base("equation")
        b["text"] = text
        return b

    # 图注/表注：panel 字母标丢弃；带编号图注/表注标记后由绑定步骤处理
    if label in _CAPTION_LABELS:
        text = clean_text(content)
        if not text or _PANEL_LABEL_RE.match(text):
            return None
        if label in ("figure_title", "vision_footnote") and _FIG_CAPTION_RE.match(text):
            b = _base("_fig_caption")
            b["text"] = text
            return b
        if label == "table_title" or _TABLE_CAPTION_RE.match(text):
            b = _base("_table_caption")
            b["text"] = text
            return b
        b = _base("text")
        b["text"] = text
        return b

    # 表格（PaddleOCR-VL 直出 HTML，单元格内可含 LaTeX）
    if label == "table":
        text = clean_text(content)
        if not text:
            return None
        b = _base("table")
        b["table_body"] = text
        b["table_caption"] = []
        return b

    # 文本类
    text = _HEADING_MARK_RE.sub("", clean_text(content))
    if not text:
        return None
    b = _base("text")
    b["text"] = text
    if label in _HEADING_LABELS:
        b["text_level"] = 1
    elif label not in _TEXT_LABELS:
        logger.debug(f"    未识别 label={label!r}，按 text 处理")
    return b


def _bind_figure_captions(items: list[dict]) -> list[dict]:
    """把 _fig_caption / _table_caption 标记绑定到图片/表格块
    （归一化为 MinerU image_caption / table_caption 语义）。

    启发式：
    - 图注后紧跟连续图片段 → 绑到该段最后一张（图注在前的版式）；
      否则绑图注前紧邻的未绑图（图注在后的版式）。
    - 表注绑同页后一个表格块，没有则绑前一个（表注几乎总在表前）。
    - 都不满足 → 降级为普通文本块，交给下游游离图注绑回逻辑。
    不绑定的话，独立的图注/表注文本块会让下游分组与跨页表合并
    拿不到边界（整篇图坍缩成一组、续表被 _has_text_between 阻断）。
    """
    n = len(items)
    for i, b in enumerate(items):
        if b["type"] == "_fig_caption":
            target = None
            j = i + 1
            while j < n and items[j]["type"] == "image":
                target = j
                j += 1
            if target is None:
                j = i - 1
                if j >= 0 and items[j]["type"] == "image" \
                        and not items[j].get("image_caption"):
                    target = j
            if target is not None:
                items[target].setdefault("image_caption", []).append(b["text"])
                b["_bound"] = True
        elif b["type"] == "_table_caption":
            target = None
            for j in range(i + 1, n):
                if items[j]["type"] == "table":
                    target = j
                    break
            if target is None:
                for j in range(i - 1, -1, -1):
                    if items[j]["type"] == "table":
                        target = j
                        break
            if target is not None:
                items[target].setdefault("table_caption", []).append(b["text"])
                b["_bound"] = True

    out = []
    for b in items:
        if b["type"] in ("_fig_caption", "_table_caption"):
            if b.get("_bound"):
                continue
            b = {"type": "text", "text": b["text"], "page_idx": b["page_idx"],
                 **({"bbox": b["bbox"]} if "bbox" in b else {})}
        out.append(b)
    return out
