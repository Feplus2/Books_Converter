"""
Stage 3: EPUB 生成

核心设计：层级嵌套渲染，杜绝内容重复。

- 编（level 1）→ 分隔页（part divider）
- 章（level 2）→ 独立 EPUB 章节（spine item）
- 节/小节（level 3-5）→ 内嵌在章内的子标题

每页内容只渲染一次，子标题在 block 流中按位置插入。
MinerU 的 text_level 不可靠——所有标题由 DeepSeek 结构分析提供。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from ebooklib import epub
import fitz  # PyMuPDF，用于渲染 PDF 首页作为封面
from PIL import Image

logger = logging.getLogger(__name__)

# ── 标点与正则常量 ──────────────────────────────────────────────

_SENTENCE_END = re.compile(r'[。！？…～;:。」』）\)】""\'?!]\s*$')
# 脚注标记（①②③...）结尾 → 段落完整，不应合并
_FOOTNOTE_END = re.compile(r'[①②③④⑤⑥⑦⑧⑨⑩]\s*$')
_BROKEN_P = re.compile(
    r'<p>([^<]*?)</p>\s*\n?\s*<p>([^<]*?)</p>',
    re.DOTALL,
)
# MinerU LaTeX 风格上标：$^{①}$ $^{②}$ 等
_LATEX_SUP = re.compile(r'\$\^\{(.+?)\}\$')
# 脚注标记（①②③...）后紧跟编号列表项（1. 2. 等）→ 需要分段
_FOOTNOTE_LIST_SPLIT = re.compile(
    r'(<sup>[①②③④⑤⑥⑦⑧⑨⑩]</sup>)\s*(\d+\.)'
)
# 顶层标题是"编/篇/卷/部"类大分区的用词特征（中/英/德/法）
_PARTITION_HINT = re.compile(
    r'^(第\s*[一二三四五六七八九十百零〇0-9]+\s*[编篇卷部]'
    r'|part\b|volume\b|book\s+[ivx0-9]|teil\b|partie\b|tome\b)',
    re.I,
)
# "章"级标题的用词特征
_CHAPTER_HINT = re.compile(
    r'^(第\s*[一二三四五六七八九十百零〇0-9]+\s*章'
    r'|chapter\b|kapitel\b|chapitre\b)',
    re.I,
)


def _spine_from_toc(toc_entries: list) -> tuple:
    """从目录条目的用词形状确定 (partition_level, spine_level)。

    目录条目层级是绝对真值（编=L1 章=L2 节=L3…），比按正文标题
    用词猜测稳健——正文切片里可能没有顶层标题（如只切了第一章）。
    无匹配时返回 (None, None)，调用方回退到启发式。
    """
    part_lv = chap_lv = None
    for e in toc_entries or []:
        text = (e.get("text") or "").strip()
        try:
            lv = int(e.get("level", 0))
        except (TypeError, ValueError):
            continue
        if lv <= 0:
            continue
        if _PARTITION_HINT.match(text):
            part_lv = lv if part_lv is None else min(part_lv, lv)
        elif _CHAPTER_HINT.match(text):
            chap_lv = lv if chap_lv is None else min(chap_lv, lv)
    if part_lv is not None and chap_lv is not None and part_lv >= chap_lv:
        part_lv = None  # 层级异常时宁可不分编
    return part_lv, chap_lv

# 页脚注编号（① ② … / 1. / [1] 等开头）
_FN_MARK = re.compile(r'^\s*([①②③④⑤⑥⑦⑧⑨⑩]|\d{1,2}[.、]|\[\d{1,2}\])')


def _fn_marker_of(text: str) -> str | None:
    """从脚注文本开头提取编号：$^{a}$（MinerU 上标）/ ① / 1. / [1] / a. / a)"""
    t = (text or "").strip()
    m = re.match(r"^\$\^\{(.+?)\}\$", t)
    if m:
        return m.group(1)
    m = _FN_MARK.match(t)
    if m:
        return m.group(1)
    m = re.match(r"^([a-z])[.)]\s", t)
    if m:
        return m.group(1)
    return None
# 英文 → 中文标点映射（用于中文书籍）
_PUNCT_MAP = str.maketrans({
    ',': '，',
    ':': '：',
    ';': '；',
    '!': '！',
    '?': '？',
    '(': '（',
    ')': '）',
})


def _normalize(text: str) -> str:
    """去除所有空白字符，用于标题文本比对"""
    return re.sub(r'[\s\u3000]+', '', text).strip()


def _convert_latex_sup(text: str) -> str:
    """将 MinerU 的 LaTeX 上标 $^{①}$ 转为 HTML <sup>①</sup>"""
    return _LATEX_SUP.sub(r'<sup>\1</sup>', text)


def _split_after_footnote(text: str) -> str:
    """脚注标记（①②③）后紧跟编号列表项（如 '2.'）时，分段显示"""
    return _FOOTNOTE_LIST_SPLIT.sub(r'\1</p>\n<p>\2', text)


# 行内/行间公式（标点转换时保护，不把数学里的半角符号转成全角）
_MATH_SPAN = re.compile(r'(\$\$.*?\$\$|\$[^$\n]+\$)', re.DOTALL)
# 公式转换用完整匹配：group(1)=行间 $$..$$, group(2)=行内 $..$
_MATH_FULL = re.compile(r'\$\$(.+?)\$\$|\$([^$\n]+?)\$', re.DOTALL)


def _convert_punctuation(text: str) -> str:
    """将英文标点转为中文标点（仅用于含中文的文本，公式段跳过）"""
    if not re.search(r'[\u4e00-\u9fff]', text):
        return text
    # 按公式段切分：偶数段为普通文本（做转换），奇数段为数学（原样保留）
    parts = _MATH_SPAN.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].translate(_PUNCT_MAP)
        # 句号：避免误转数字中的点（如 3.14）
        parts[i] = re.sub(r'(?<!\d)\.(?!\d)', '。', parts[i])
    return ''.join(parts)


def _escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _latex_to_mathml(latex: str, display: bool) -> str:
    """LaTeX → MathML（失败返回 None）。"""
    try:
        from latex2mathml.converter import convert as _l2m
        mathml = _l2m(latex)
        # 注入源码备份（阅读器不支持 MathML 时的兜底文本）与显示模式
        mode = "block" if display else "inline"
        mathml = mathml.replace(
            "<math ",
            f'<math alttext="{_escape_attr(latex)}" display="{mode}" ',
            1,
        )
        return mathml
    except Exception:
        return None


def _mathmlify(html_text: str) -> str:
    """把文本中的 $…$ / $$…$$ 公式替换为 MathML；失败的保留 LaTeX 源码。

    EPUB 3 官方数学方案（calibre/Apple Books/Thorium 支持）；
    alttext 带 LaTeX 源码兜底，latex2mathml 转不动的表达式退化为 <code>。
    """
    def repl(m):
        latex = (m.group(1) or m.group(2) or "").strip()
        display = m.group(1) is not None
        if not latex:
            return m.group(0)
        mathml = _latex_to_mathml(latex, display)
        if mathml is not None:
            return mathml
        return f'<code class="latex">{_escape_attr(latex)}</code>'

    return _MATH_FULL.sub(repl, html_text)


# 显示型公式被解析引擎以单 $ 定界输出的识别（升格为 block 的判定）：
# 整段唯一数学区 + LaTeX 含显示结构命令 + 前后仅标点/编号。
# 高等数学 2026-08 实测：840 处例题/推导公式因此被标 inline，贴左带
# 首行缩进、行内字号——用户观感即"公式不居中"。
_DISPLAY_CMD_RE = re.compile(r"\\(?:frac|dfrac|sum|int|iint|prod|lim|sqrt|left|right|begin|overline|underbrace|operatorname)")
_PUNCT_NUM_RE = re.compile(r"[，。、;；:：,.\s（）()\[\]0-9-]+")
_P_LONE_MATH_RE = re.compile(
    r'<p>(?P<pre>[^<$]{0,6})'
    r'(?P<math><math[^>]*display="inline"[^>]*>.*?</math>)'
    r'(?P<post>[^<]{0,12})</p>',
    re.S,
)


def promote_lone_display_math(html: str) -> str:
    """段落级后处理：独占段落的行内显示公式升格为 display="block"。

    只动"前后无实义文字"的整段公式（行内引用如 "$x$，即 …" 的段落不会命中：
    其 post 含叙述字词）。幂等：display 已是 block 的不匹配该正则。"""

    def repl(m: re.Match) -> str:
        pre, math_el, post = m.group("pre"), m.group("math"), m.group("post")
        alt = re.search(r'alttext="([^"]+)"', math_el)
        latex = alt.group(1) if alt else ""
        if not (_DISPLAY_CMD_RE.search(latex) and len(latex) > 25):
            return m.group(0)
        if _PUNCT_NUM_RE.sub("", pre) or _PUNCT_NUM_RE.sub("", post):
            return m.group(0)
        promoted = math_el.replace('display="inline"', 'display="block"', 1)
        return f"<p>{pre}{promoted}{post}</p>"

    return _P_LONE_MATH_RE.sub(repl, html)


# ── 段落合并 ────────────────────────────────────────────────────

def _merge_broken_paragraphs(html: str) -> str:
    """合并因跨页扫描而异常断裂的段落"""
    prev = None
    while prev != html:
        prev = html
        html = _BROKEN_P.sub(_merge_if_broken, html)
    return html


def _merge_if_broken(m: re.Match) -> str:
    p1 = m.group(1).strip()
    p2 = m.group(2).strip()
    if not p2:
        return m.group(0)
    # 第一段以脚注标记结尾（①②③...）→ 段落完整，不合并
    if _FOOTNOTE_END.search(p1):
        return m.group(0)
    # 第二段以编号开头（如 "2."、"一、"）且较短 → 可能是新条目，不合并
    if re.match(r'^\d+[\.\、]', p2) and len(p2) <= 30:
        return m.group(0)
    # 第一段不以句末标点结尾 → 合并
    if not _SENTENCE_END.search(p1) and not _looks_like_heading(p1):
        return f"<p>{p1}{p2}</p>"
    # 第二段极短（扫描跨页碎片）→ 合并
    if len(p2) <= 30 and not _looks_like_heading(p2):
        return f"<p>{p1}{p2}</p>"
    return m.group(0)


def _looks_like_heading(text: str) -> bool:
    """是否为疑似标题行（不应合并）"""
    t = text.strip()
    return len(t) <= 25 and not t.endswith("。")


# ── CSS ─────────────────────────────────────────────────────────

DEFAULT_CSS = """
body {
    font-family: "Songti SC", "Noto Serif CJK SC", "STSong", serif;
    line-height: 1.8;
    margin: 2% 3%;
    color: #333;
}
h1 {
    text-align: center;
    font-size: 1.6em;
    margin: 2em 0 1em;
    font-weight: bold;
}
h1.part-title {
    font-size: 2em;
    margin: 30% 0 1em;
}
h2 {
    text-align: center;
    font-size: 1.4em;
    margin: 1.5em 0 0.8em;
    font-weight: bold;
}
h3 {
    text-align: left;
    font-size: 1.2em;
    margin: 1.2em 0 0.6em;
    font-weight: bold;
}
h4 {
    text-align: left;
    font-size: 1.1em;
    margin: 1em 0 0.4em;
    font-weight: bold;
}
h5 {
    text-align: left;
    font-size: 1.0em;
    margin: 0.8em 0 0.3em;
    font-weight: bold;
}
p {
    text-indent: 2em;
    margin: 0.3em 0;
}
p.no_indent {
    text-indent: 0;
}
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em auto;
}
table {
    border-collapse: collapse;
    width: fit-content;
    max-width: 100%;
    margin: 1em auto;
}
table td, table th {
    border: 1px solid #ccc;
    padding: 4px 8px;
}
blockquote {
    margin: 1em 2em;
    color: #555;
}
p.footnote {
    font-size: 0.85em;
    text-indent: 0;
    margin: 0.2em 0;
    color: #555;
}
aside.footnotes hr {
    width: 30%;
    margin: 1.5em 0 0.8em 0;
    border: none;
    border-top: 1px solid #ccc;
}
sup {
    font-size: 0.75em;
}
code.latex {
    font-family: "Cambria Math", "Palatino Linotype", serif;
    font-size: 0.95em;
    background: #f5f5f5;
    padding: 0 2px;
}
math {
    font-size: 1.05em;
}
math[display="block"] {
    display: block;
    text-align: center;
    margin: 0.8em 0;
}
"""


# ── 内容分类 ────────────────────────────────────────────────────

def _classify_blocks_by_page(content_list: list) -> dict:
    """将 content_list 按页码分组（1-based 页码），保留原始索引供译文查找"""
    pages = {}
    for idx, block in enumerate(content_list):
        block["_idx"] = str(idx)
        page = block.get("page_idx", 0) + 1
        if page not in pages:
            pages[page] = []
        pages[page].append(block)
    return pages


def _translation_of(block: dict, translations: dict | None) -> str | None:
    """按 source_id 查译文（含 .capN 后缀回退到父块）"""
    if not translations:
        return None
    sid = str(block.get("source_id", ""))
    key = sid.rsplit(":", 1)[-1]
    zh = translations.get(key)
    if zh is None and ("." in key):
        zh = translations.get(key.split(".")[0])
    if zh is None:
        zh = translations.get(block.get("_idx", ""))
    return zh


# ── Block → HTML 渲染 ──────────────────────────────────────────

def _render_block_to_html(block: dict, images_dir: str,
                          chinese_punct: bool = False,
                          translations: dict = None) -> str:
    """将单个 content block 渲染为 HTML。

    核心原则：**永不丢弃内容**。所有 block 都有 HTML 输出。
    标题判断由 `complete_outline` 负责，此处只做忠实渲染。
    translations 提供时优先使用译文（仅文本类 block）。
    """
    btype = block.get("type", "")
    text = block.get("text", "")

    if btype in ("text", "paragraph"):
        if not text.strip():
            return ""
        text = _translation_of(block, translations) or text
        text = _convert_latex_sup(text)
        if chinese_punct:
            text = _convert_punctuation(text)
        text = _mathmlify(text)
        text = _split_after_footnote(text)
        return f"<p>{text}</p>"

    elif btype == "title":
        # 也渲染为 <p>，由 complete_outline 决定是否为标题
        if not text.strip():
            return ""
        text = _translation_of(block, translations) or text
        text = _convert_latex_sup(text)
        if chinese_punct:
            text = _convert_punctuation(text)
        text = _mathmlify(text)
        text = _split_after_footnote(text)
        return f"<p>{text}</p>"

    elif btype == "image":
        img_path = block.get("img_path", "") or block.get("image_path", "")
        caption = block.get("image_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        img_name = Path(img_path).name if img_path else ""
        html = ""
        if img_name:
            html = f'<img src="images/{img_name}" alt="{caption}"/>'
        if caption:
            if chinese_punct:
                caption = _convert_punctuation(caption)
            html += f'<p class="no_indent"><small>{_mathmlify(caption)}</small></p>'
        return html

    elif btype == "table":
        body = block.get("table_body", "")
        caption = block.get("table_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        html = ""
        if caption:
            if chinese_punct:
                caption = _convert_punctuation(caption)
            html += f'<p class="no_indent"><strong>{_mathmlify(caption)}</strong></p>'
        html += _mathmlify(body)
        return html

    elif btype == "list":
        items = block.get("list_items", [])
        if not items:
            return ""
        items_html = "\n".join(f"<li>{_mathmlify(item)}</li>" for item in items)
        return f"<ul>{items_html}</ul>"

    elif btype == "equation":
        latex = block.get("latex", "") or block.get("text", "")
        # 兼容 latex/text 字段形态：有 $…$ 定界走 MathML，纯 LaTeX 源码也尝试转换
        if "$" in latex:
            return f'<p class="no_indent">{_mathmlify(latex)}</p>'
        mathml = _latex_to_mathml(latex, True)
        if mathml is not None:
            return f'<p class="no_indent">{mathml}</p>'
        return f'<p class="no_indent"><code class="latex">{_escape_attr(latex)}</code></p>'

    elif btype == "code":
        code = block.get("code_body", "") or block.get("text", "")
        lang = block.get("code_language", "")
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f"<pre><code{lang_attr}>{code}</code></pre>"

    elif btype in ("header", "footer", "page_number", "page_footnote",
                    "aside_text"):
        # 页眉页脚等噪音，跳过
        return ""

    elif btype == "chart":
        return ""

    else:
        if text.strip():
            if chinese_punct:
                text = _convert_punctuation(text)
            return f"<p>{text}</p>"
        return ""


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


# ── 章节渲染（核心：声明式渲染，不丢内容） ──────────────────────
    return 0


# ── 层级分组 ────────────────────────────────────────────────────

def _determine_spine_level(body_items: list) -> int:
    """确定 spine 层级：第二个最小 level（通常是"章"）。

    如果只有一种层级，就用那个层级。
    """
    levels = sorted(set(item.get("level", 1) for item in body_items))
    if len(levels) >= 2:
        return levels[1]
    return levels[0] if levels else 1


def _build_spine_groups(body_items: list, spine_level: int) -> list:
    """将 body 扁平列表分组为 spine 组。

    返回结构：
    [
        {
            "parent": {level 1 item} or None,
            "children": [
                {
                    "item": {spine-level item},
                    "sub_headings": [{level 3+ items}, ...]
                }, ...
            ]
        }, ...
    ]
    """
    all_levels = sorted(set(item.get("level", 1) for item in body_items))

    # 找到 spine_level 之上的父级层级（通常是编）
    parent_level = None
    for lv in reversed(all_levels):
        if lv < spine_level:
            parent_level = lv
            break

    groups = []
    current_child_entry = None

    for item in body_items:
        level = item.get("level", 1)

        if parent_level is not None and level == parent_level:
            # 新的父级（编）
            groups.append({"parent": item, "children": []})

        elif level == spine_level:
            # 新的 spine 项（章）
            current_child_entry = {"item": item, "sub_headings": []}
            if groups:
                groups[-1]["children"].append(current_child_entry)
            else:
                # 无父级
                groups.append({"parent": None, "children": [current_child_entry]})

        elif level > spine_level:
            # 子标题
            if current_child_entry:
                current_child_entry["sub_headings"].append(item)

    return groups


# ── 章节渲染（核心：声明式渲染，不丢内容） ──────────────────────

def _render_chapter_html(
    ch_item: dict,
    sub_headings: list,
    pages: dict,
    images_dir: str,
    noise_pages: set,
    chinese_punct: bool,
    complete_outline: list = None,
) -> str:
    """渲染一个章（spine item）的完整 HTML。

    核心原则：**永不丢弃内容**。
    - 以章标题开头（h2）
    - 逐页渲染 content blocks
    - 遇到匹配 `complete_outline` 中标题的 block → 渲染为 `<h{level}>`
    - 其他所有 block → 渲染为 `<p>` 或对应的 HTML
    - **绝不跳过任何 block**（除非是页眉页脚等噪音类型）
    """
    ch_level = ch_item.get("level", 2)
    ch_tag = f"h{min(ch_level, 2)}"
    ch_title_html = _mathmlify(_convert_latex_sup(ch_item["title"]))
    parts = [f'<{ch_tag} class="chapter-title">{ch_title_html}</{ch_tag}>']

    # 构建标题查找表（归一化文本 → 标题信息）
    heading_lookup = {}

    # 1. 从 sub_headings（来自 body 结构）添加
    for sub in sub_headings:
        key = _normalize(sub["title"])
        heading_lookup[key] = {"level": sub.get("level", 3), "title": sub["title"]}

    # 2. 从 complete_outline 添加（覆盖/补充）
    if complete_outline:
        for item in complete_outline:
            if item.get("status") == "heading" or "level" in item:
                text = item.get("text", "")
                if text:
                    key = _normalize(text)
                    # 只添加 page 在当前章范围内的
                    page = item.get("page", 0)
                    if ch_item["page_start"] <= page <= ch_item["page_end"]:
                        heading_lookup[key] = {"level": item["level"], "title": text}

    # 章标题本身也需要跳过（避免重复渲染）
    ch_title_normalized = _normalize(ch_item["title"])

    # 逐页渲染
    for page in range(ch_item["page_start"], ch_item["page_end"] + 1):
        if page in noise_pages:
            continue

        for block in pages.get(page, []):
            text = block.get("text", "").strip()
            btype = block.get("type", "")

            # 页眉页脚等噪音类型，跳过
            if btype in ("header", "footer", "page_number", "page_footnote", "aside_text"):
                continue

            # 检查是否匹配标题 → 渲染为 h-tag
            normalized = _normalize(text) if text else ""
            if normalized and normalized in heading_lookup:
                h_info = heading_lookup[normalized]
                htag = f"h{min(h_info['level'], 6)}"
                h_text = _convert_latex_sup(h_info['title'])
                parts.append(f"<{htag}>{h_text}</{htag}>")
                continue

            # 跳过章标题（避免重复）
            if normalized and normalized == ch_title_normalized:
                continue

            # 其他所有 block → 正常渲染（不丢内容）
            rendered = _render_block_to_html(block, images_dir, chinese_punct)
            if rendered:
                parts.append(rendered)

    result = "\n".join(parts)
    result = _merge_broken_paragraphs(result)
    return result


def _render_pages_html(
    page_start: int,
    page_end: int,
    pages: dict,
    images_dir: str,
    noise_pages: set,
    chinese_punct: bool = False,
    translations: dict = None,
) -> str:
    """渲染指定页码范围的纯内容（无标题覆写，用于前页/后页/分隔页）"""
    html_parts = []
    for pn in range(page_start, page_end + 1):
        if pn in noise_pages:
            continue
        for block in pages.get(pn, []):
            btype = block.get("type", "")
            # 前页/后页中跳过 title 块（直接渲染为段落）
            if btype == "title":
                text = _translation_of(block, translations) or block.get("text", "")
                if text.strip():
                    level = block.get("text_level", 0) or 1
                    tag = f"h{min(level + 1, 4)}"
                    html_parts.append(f"<{tag}>{_convert_latex_sup(text)}</{tag}>")
                continue
            rendered = _render_block_to_html(block, images_dir, chinese_punct,
                                             translations)
            if rendered:
                html_parts.append(rendered)
    return "\n".join(html_parts)


# ── Popo 引擎渲染 ─────────────────────────────────────────────

def _body_range(structure: dict, total_pages: int) -> tuple:
    """正文页码范围：front_matter 之后 ~ back_matter 之前"""
    start = 1
    for fm in structure.get("front_matter", []):
        try:
            start = max(start, int(fm.get("page_end", 0)) + 1)
        except (TypeError, ValueError):
            pass
    end = total_pages
    for bm in structure.get("back_matter", []):
        try:
            ps = int(bm.get("page_start", 0))
        except (TypeError, ValueError):
            continue
        if ps > 0:
            end = min(end, ps - 1)
    return start, max(end, start)


def _dehyphen_join(left: str, right: str) -> str:
    """跨页段落拼接：处理英文断词连字符与词间空格"""
    if left.endswith("-") and right[:1].isascii() and right[:1].isalpha():
        return left[:-1] + right
    if left and right:
        # 两边都是 ASCII 字母/数字 → 补空格；含 CJK → 直接连
        if (left[-1].isascii() and left[-1].isalnum()
                and right[0].isascii() and right[0].isalnum()):
            return left + " " + right
    return left + right


def _build_toc_lookup(toc_entries: list) -> dict:
    """目录条目 → 查找表：归一化文本 → 清理后的展示文本（去页码/点线）"""
    lookup = {}
    for e in toc_entries or []:
        raw = (e.get("text") or "").strip()
        if not raw:
            continue
        display = re.sub(r'[\s.…·_]+\d+\s*$', '', raw).strip()
        key = _normalize(display)
        if key and key not in lookup:
            lookup[key] = display
    return lookup


def _enrich_title(title: str, toc_lookup: dict) -> str:
    """裸标题（'第一章'）→ 目录完整标题（'第一章 蠢材的天堂'）。

    精确归一化匹配直接采用；否则取以裸标题为前缀的最长目录条目。
    无匹配则保留原标题。
    """
    key = _normalize(title)
    if not key or not toc_lookup:
        return title
    best = None
    for k, display in toc_lookup.items():
        if k == key:
            return display
        if k.startswith(key) and len(k) > len(key):
            if best is None or len(k) > len(best[0]):
                best = (k, display)
    return best[1] if best else title


def _render_popo_body(popo_blocks: list, content_list: list,
                      body_start: int, body_end: int,
                      chinese_punct: bool, default_title: str,
                      toc_entries: list = None,
                      translations: dict = None) -> list:
    """线性遍历 Popo 标注 blocks，按层级切成 编(divider)/章(chapter) 单元。

    返回 units 列表：
      {"kind": "divider"|"chapter", "title": str, "level": int,
       "parts": [html...], "subs": [(level, title, anchor)]}

    应用的 Popo 标注：
    - title + level → 编/章边界或章内子标题（h{level}，带锚点）
    - contd → 跨页段落拼接（目标块并入上一段，不断开）
    - table_merge → 跨页表格已合并（merge_cross_page_tables）
    - image 关联 → caption/footnote 挂到所属图表下
    """
    from popo.table_merge_utils import merge_cross_page_tables

    blocks = [b for b in popo_blocks
              if body_start <= b.get("page", 0) <= body_end]
    blocks = merge_cross_page_tables(blocks)

    # contd 目标集合：id 在其中的块是上一段的跨页延续
    contd_targets = {b["contd"] for b in blocks if b.get("contd", -1) >= 0}

    # popo block id → MinerU 图片文件名（经 source_id 映射回 content_list）
    img_lookup = {}
    for b in blocks:
        if b.get("type") != "image":
            continue
        try:
            idx = int(str(b.get("source_id", "")).rsplit(":", 1)[1])
            src = content_list[idx]
            img_path = src.get("img_path", "") or src.get("image_path", "")
            if img_path:
                img_lookup[b["id"]] = Path(img_path).name
        except (ValueError, IndexError, TypeError):
            pass

    # caption/footnote 两阶段：先收集关联到视觉块的，孤儿按普通段落渲染
    caption_types = ("image_caption", "table_caption")
    footnote_types = ("image_footnote", "table_footnote")
    caption_map = {}
    footnote_map = {}
    for b in blocks:
        btype = b.get("type", "")
        text = (b.get("content") or "").strip()
        if not text:
            continue
        target = b.get("image", -1)
        text = _translation_of(b, translations) or text
        if btype in caption_types and target >= 0:
            caption_map.setdefault(target, []).append(text)
        elif btype in footnote_types and target >= 0:
            footnote_map.setdefault(target, []).append(text)

    def convert(text: str) -> str:
        text = _convert_latex_sup(text)
        if chinese_punct:
            text = _convert_punctuation(text)
        return text

    # 页脚注收集：page → [{marker, text, claimed}]（type=page_footnote 的块）
    page_fn_map = {}
    for b in blocks:
        if b.get("type") != "page_footnote":
            continue
        t = (b.get("content") or "").strip()
        if not t:
            continue
        page_fn_map.setdefault(b.get("page", 0), []).append({
            "marker": _fn_marker_of(t),
            "text": _translation_of(b, translations) or t,
            "claimed": False, "id": None, "backlink": None,
        })
    n_page_footnotes = sum(len(v) for v in page_fn_map.values())
    if n_page_footnotes:
        logger.info(f"  页脚注: {n_page_footnotes} 条待锚定")

    # 层级 → spine/partition：优先用目录条目的用词形状（绝对真值），
    # 无目录信息时回退到正文顶层标题的用词启发式。
    # 注意：Popo 的 level 只有相对意义，不能假设绝对层级。
    partition_level, spine_level = _spine_from_toc(toc_entries)
    if spine_level is None:
        title_levels = sorted({
            b["level"] for b in blocks
            if b.get("type") == "title" and b.get("level", -1) > 0
        })
        partition_level, spine_level = None, (title_levels[0] if title_levels else 1)
        if len(title_levels) >= 2:
            top_titles = [
                (b.get("content") or "").strip() for b in blocks
                if b.get("type") == "title" and b.get("level") == title_levels[0]
            ]
            n_hint = sum(1 for t in top_titles if _PARTITION_HINT.match(t))
            if n_hint >= max(1, len(top_titles) // 2):
                partition_level, spine_level = title_levels[0], title_levels[1]
    logger.info(
        f"  Popo 层级: partition={partition_level}, spine={spine_level}"
    )

    units = []
    cur = None
    open_para = None
    toc_lookup = _build_toc_lookup(toc_entries)
    prev_unit_key = None  # 上一个编/章单元的归一化标题（查重）
    fn_counter = [0]      # 脚注序号（全书唯一 id）
    last_page = 0         # 当前遍历到的页码（未锚定脚注的清扫水位）

    def _similar(a, b):
        """相邻单元标题判重：互为包含视为同一编/章（如 '权利变动' vs '第四编权利变动'）"""
        return a and b and (a in b or b in a)

    def claim_footnotes(seg: str, page: int) -> str:
        """把 seg 中能锚定的脚注标记替换为 noteref 链接，脚注挂到当前单元"""
        for fn in page_fn_map.get(page, []):
            if fn["claimed"] or not fn["marker"]:
                continue
            mark = fn["marker"]
            # 上标形式 <sup>a</sup> 必试；圈码标记（非字母数字）才退化裸匹配，
            # 字母/数字标记只允许上标（防误伤正文）
            patterns = [f"<sup>{mark}</sup>"]
            if mark and not mark[0].isalnum():
                patterns.append(mark)
            for pat in patterns:
                pos = seg.find(pat)
                if pos < 0:
                    continue
                fn_counter[0] += 1
                n = fn_counter[0]
                link = (f'<a epub:type="noteref" id="fnref_{n}" '
                        f'href="#fn_{n}">{pat}</a>')
                seg = seg[:pos] + link + seg[pos + len(pat):]
                fn["claimed"] = True
                fn["id"] = n
                fn["backlink"] = f"fnref_{n}"
                cur.setdefault("fn_list", []).append(fn)
                break
        return seg

    def flush_footnotes():
        """单元收尾：渲染章末尾注（含未锚定的兜底，绝不丢内容）"""
        if cur is None:
            return
        # 未锚定的脚注：凡页码不超过当前水位的，随本单元一并收尾
        for page in sorted(page_fn_map):
            if page > last_page:
                break
            for fn in page_fn_map[page]:
                if not fn["claimed"]:
                    fn["claimed"] = True
                    cur.setdefault("fn_list", []).append(fn)
        fns = cur.get("fn_list", [])
        if not fns:
            return
        items = []
        for fn in fns:
            # 脚注正文自带的编号剥掉（含 $^{a}$ 形式），避免与链接编号重复
            fn_text = fn["text"]
            if fn.get("marker"):
                for pref in (f'$^{{{fn["marker"]}}}$', fn["marker"]):
                    if fn_text.startswith(pref):
                        fn_text = fn_text[len(pref):].lstrip(" .、．")
                        break
            if fn.get("id"):
                back = (f'<a href="#{fn["backlink"]}">{fn["marker"] or "※"}</a> ')
                fid = f' id="fn_{fn["id"]}"'
            else:
                back = f'{fn["marker"] or "※"} '
                fid = ""
            items.append(
                f'<p class="footnote"{fid}>{back}{_mathmlify(convert(fn_text))}</p>')
        cur["parts"].append(
            '<aside class="footnotes"><hr/>' + "".join(items) + "</aside>")
        cur["fn_list"] = []

    def flush_para():
        nonlocal open_para
        if open_para and cur is not None:
            cur["parts"].append(f"<p>{open_para}</p>")
        open_para = None

    def ensure_unit():
        nonlocal cur
        if cur is None:
            cur = {"kind": "chapter", "title": default_title,
                   "level": spine_level, "parts": [], "subs": [], "fn_list": []}
            units.append(cur)

    for b in blocks:
        btype = b.get("type", "text")
        text = (b.get("content") or "").strip()
        level = b.get("level", -1)
        last_page = b.get("page", last_page)

        # ── 标题 ──
        if btype == "title" and level > 0:
            flush_para()
            flush_footnotes()
            zh = _translation_of(b, translations)
            display = zh if zh else _enrich_title(text, toc_lookup)
            dkey = _normalize(display)
            if partition_level is not None and level <= partition_level:
                if _similar(dkey, prev_unit_key) and cur is not None:
                    # 相似分隔页（如 '权利变动' 与 '第四编'）→ 合并进当前单元
                    if len(dkey) > len(prev_unit_key) or _PARTITION_HINT.match(display):
                        cur["title"] = display
                        prev_unit_key = _normalize(display)
                    continue
                cur = {"kind": "divider", "title": display, "level": level,
                       "parts": [], "subs": [], "fn_list": []}
                units.append(cur)
                prev_unit_key = dkey
            elif level <= spine_level:
                if _similar(dkey, prev_unit_key) and cur is not None:
                    # 重复/相似章标题（章题页重复、分隔页碎片）→ 不新开章
                    if len(dkey) > len(prev_unit_key) or _PARTITION_HINT.match(display):
                        cur["title"] = display
                        prev_unit_key = _normalize(display)
                    continue
                cur = {"kind": "chapter", "title": display, "level": level,
                       "parts": [], "subs": [], "fn_list": []}
                units.append(cur)
                prev_unit_key = dkey
            else:
                ensure_unit()
                anchor = f"h{b['id']}"
                htag = f"h{min(level, 6)}"
                cur["parts"].append(f'<{htag} id="{anchor}">{_mathmlify(convert(display))}</{htag}>')
                if level == spine_level + 1:
                    cur["subs"].append((level, display, anchor))
            continue

        # ── 页脚注：收集待锚定，章末尾注统一渲染 ──
        if btype == "page_footnote":
            continue

        # ── 图表标题/脚注：已关联的跳过（随视觉块渲染），孤儿按段落渲染 ──
        if btype in caption_types + footnote_types:
            if b.get("image", -1) >= 0:
                continue
            if not text:
                continue
            # 落入普通段落流程

        # ── 图片 ──
        elif btype == "image":
            flush_para()
            ensure_unit()
            img_name = img_lookup.get(b["id"], "")
            html = ""
            if img_name:
                caps = caption_map.get(b["id"], [])
                alt = caps[0] if caps else ""
                html += f'<img src="images/{img_name}" alt="{convert(alt)}"/>'
            for cap in caption_map.get(b["id"], []):
                html += f'<p class="no_indent"><small>{_mathmlify(convert(cap))}</small></p>'
            for fn in footnote_map.get(b["id"], []):
                html += f'<p class="no_indent"><small>{_mathmlify(convert(fn))}</small></p>'
            if html:
                cur["parts"].append(html)
            continue

        # ── 表格（跨页合并后的 html） ──
        elif btype == "table":
            flush_para()
            ensure_unit()
            html = ""
            for cap in caption_map.get(b["id"], []):
                html += f'<p class="no_indent"><strong>{_mathmlify(convert(cap))}</strong></p>'
            # 表体 HTML 内的 $…$（MinerU 单元格公式）一并转 MathML
            html += _mathmlify(b.get("content", "") or "")
            for fn in footnote_map.get(b["id"], []):
                html += f'<p class="no_indent"><small>{_mathmlify(convert(fn))}</small></p>'
            if html.strip():
                cur["parts"].append(html)
            continue

        # ── 文本 / 公式 / 其他 ──
        if not text:
            continue
        ensure_unit()
        raw = _translation_of(b, translations) or text
        seg = convert(raw)
        seg = claim_footnotes(seg, b.get("page", 0))
        seg = _mathmlify(seg)
        if b.get("id") in contd_targets and open_para is not None:
            open_para = _dehyphen_join(open_para, seg)
        else:
            flush_para()
            open_para = seg

    flush_para()
    flush_footnotes()
    return units


def _emit_popo_body(units: list, book, spine: list, css,
                    chapter_idx: int, lang: str) -> tuple:
    """把 _render_popo_body 的单元写入 EPUB，返回 (body_spine_info, chapter_idx)。

    TOC 结构（最多 3 层）：
    - 编(divider) → Section 嵌套章链接
    - 章(chapter) → Link；有子标题的章 → (Section(href), (子标题锚点链接...))
    """
    body_spine_info = []
    group_info = {"parent_link": None, "children_links": []}

    def close_group():
        nonlocal group_info
        if group_info["parent_link"] or group_info["children_links"]:
            body_spine_info.append(group_info)
        group_info = {"parent_link": None, "children_links": []}

    for unit in units:
        if unit["kind"] == "divider":
            close_group()
            parts = [
                f'<h1 class="part-title">{_mathmlify(_convert_latex_sup(unit["title"]))}</h1>'
            ] + unit["parts"]
            divider = epub.EpubHtml(
                title=unit["title"],
                file_name=f"part_{chapter_idx:03d}.xhtml",
                lang=lang,
            )
            divider.content = "\n".join(parts)
            if "<math" in divider.content:
                divider.properties.append("mathml")
            divider.add_item(css)
            book.add_item(divider)
            spine.append(divider)
            group_info = {
                "parent_link": epub.Link(
                    divider.file_name, unit["title"], f"part_{chapter_idx}"
                ),
                "children_links": [],
            }
            chapter_idx += 1
        else:
            parts = [
                f'<h2 class="chapter-title">{_mathmlify(_convert_latex_sup(unit["title"]))}</h2>'
            ] + unit["parts"]
            file_name = f"chapter_{chapter_idx:03d}.xhtml"
            chapter = epub.EpubHtml(
                title=unit["title"], file_name=file_name, lang=lang
            )
            chapter.content = promote_lone_display_math("\n".join(parts))
            if "<math" in chapter.content:
                chapter.properties.append("mathml")
            chapter.add_item(css)
            book.add_item(chapter)
            spine.append(chapter)
            if unit["subs"]:
                sub_links = tuple(
                    epub.Link(f"{file_name}#{anchor}", sub_title,
                              f"sub_{chapter_idx}_{k}")
                    for k, (_lv, sub_title, anchor) in enumerate(unit["subs"])
                )
                group_info["children_links"].append(
                    (epub.Section(unit["title"], href=file_name), sub_links)
                )
            else:
                group_info["children_links"].append(
                    epub.Link(file_name, unit["title"], f"ch_{chapter_idx}")
                )
            chapter_idx += 1

    close_group()
    return body_spine_info, chapter_idx


# ── 图片收集 ────────────────────────────────────────────────────

def _collect_images(content_list: list, images_dir: str) -> list:
    """从 content_list 中提取图片信息，尝试从 images_dir 或 CWD 加载"""
    image_info = []
    seen = set()

    for block in content_list:
        if block.get("type") != "image":
            continue
        img_path = block.get("img_path", "") or block.get("image_path", "")
        if not img_path:
            continue
        img_name = Path(img_path).name
        if img_name in seen:
            continue
        seen.add(img_name)
        image_info.append((img_name, img_path))

    # 搜索图片文件的候选目录
    search_dirs = []
    if images_dir and os.path.isdir(images_dir):
        search_dirs.append(images_dir)
    # MinerU SDK 可能将图片保存在 CWD/images/ 下
    cwd_images = Path("images")
    if cwd_images.is_dir():
        search_dirs.append(str(cwd_images))

    loaded = []
    for img_name, img_path in image_info:
        for search_dir in search_dirs:
            candidate = Path(search_dir) / img_name
            if candidate.is_file():
                loaded.append((img_name, str(candidate)))
                break
        else:
            # 也尝试直接用 img_path（如果是绝对路径）
            if os.path.isabs(img_path) and os.path.isfile(img_path):
                loaded.append((img_name, img_path))

    return loaded


# ── 封面提取 ────────────────────────────────────────────────────

def _extract_cover_image(pdf_path: str, output_dir: str) -> Optional[str]:
    """从 PDF 首页渲染高清封面图片

    Returns:
        封面图片路径，失败时返回 None
    """
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        # 2x 缩放 = 约 144 DPI，画质足够且体积可控
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)

        cover_path = Path(output_dir) / "cover.jpg"
        pix.save(str(cover_path))
        doc.close()
        logger.info(f"  封面: 从 PDF 首页提取 ({pix.width}x{pix.height})")
        return str(cover_path)
    except Exception as e:
        logger.warning(f"  封面提取失败: {e}")
        return None


# ── 主函数 ──────────────────────────────────────────────────────

def generate_epub(
    book_name: str,
    mineru_info: dict,
    structure: dict,
    output_dir: str,
    pdf_path: str = "",
    translations: dict = None,
) -> Path:
    """
    生成 EPUB 文件。

    Args:
        book_name: 书名
        mineru_info: Stage 1 输出 {markdown, content_list, images_dir}
        structure: Stage 2 输出 {metadata, front_matter, body, back_matter, noise_ranges}
        output_dir: 输出目录
        pdf_path: 源 PDF 路径（用于提取封面）
        translations: Stage 4 输出 {key: 译文}（可选，提供时正文/前后页用译文渲染）
    """
    logger.info("Stage 3 开始: 生成 EPUB")

    content_list = mineru_info.get("content_list", [])
    images_dir = mineru_info.get("images_dir", "")
    meta = structure.get("metadata", {})
    body_items = structure.get("body", [])

    # 噪音页（只跳过空白页）
    noise_pages = set()
    for noise in structure.get("noise_ranges", []):
        if noise.get("type") == "blank_page":
            for p in noise.get("pages", []):
                noise_pages.add(p)

    # 按页组织 blocks
    pages = _classify_blocks_by_page(content_list)

    # Popo/Hybrid 引擎：加载标注 blocks 与正文页码范围
    popo_blocks = None
    popo_body = None
    if structure.get("engine") in ("popo", "hybrid"):
        blocks_file = structure.get("popo_blocks_file")
        blocks_path = Path(output_dir) / blocks_file if blocks_file else None
        if blocks_path and blocks_path.is_file():
            with open(blocks_path, "r", encoding="utf-8") as f:
                popo_blocks = json.load(f)
            total_pages = max(
                (b.get("page_idx", 0) + 1 for b in content_list), default=0
            )
            popo_body = _body_range(structure, total_pages)
            logger.info(
                f"  Popo 引擎: {len(popo_blocks)} 标注块, "
                f"正文页范围 {popo_body[0]}-{popo_body[1]}"
            )
        else:
            logger.error("  Popo 引擎但找不到标注 blocks 文件，正文将为空")

    # 确定 spine 层级和分组（旧引擎；Popo 引擎 body 为空，以下分组自然跳过）
    spine_level = _determine_spine_level(body_items)
    spine_groups = _build_spine_groups(body_items, spine_level)
    logger.info(
        f"  层级: spine=level {spine_level}, "
        f"{len(spine_groups)} 个分组, "
        f"{sum(len(g['children']) for g in spine_groups)} 个 spine 章节"
    )

    # 是否中文标点转换
    chinese_punct = meta.get("language", "zh") == "zh"

    # 创建 EPUB
    book = epub.EpubBook()

    # 元数据
    title = meta.get("title") or book_name
    book.set_identifier(f"books-converter-{hash(title) & 0xFFFFFFFF:08x}")
    book.set_title(title)
    book.set_language(meta.get("language", "zh"))

    authors = meta.get("authors", [])
    if authors:
        for author in authors:
            book.add_author(author)

    translator = meta.get("translator")
    if translator and translator != "null":
        book.add_metadata("DC", "contributor", translator, {"role": "trl"})

    publisher = meta.get("publisher")
    if publisher and publisher != "null":
        book.add_metadata("DC", "publisher", publisher)

    # 封面（从 PDF 首页截图）
    cover_image_path = None
    if pdf_path and os.path.isfile(pdf_path):
        cover_img_path = _extract_cover_image(pdf_path, output_dir)
        if cover_img_path and os.path.isfile(cover_img_path):
            cover_image_path = cover_img_path
            logger.info(f"  封面已提取: cover.jpg")

    # CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=DEFAULT_CSS,
    )
    book.add_item(css)

    # 图片
    loaded_images = _collect_images(content_list, images_dir)
    for img_name, img_path in loaded_images:
        try:
            with open(img_path, "rb") as f:
                img_data = f.read()
            ext = Path(img_name).suffix.lower().lstrip(".")
            media_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
            item = epub.EpubItem(
                uid=f"img_{img_name}",
                file_name=f"images/{img_name}",
                media_type=media_type,
                content=img_data,
            )
            book.add_item(item)
        except Exception as e:
            logger.warning(f"  无法添加图片 {img_name}: {e}")
    if loaded_images:
        logger.info(f"  加载 {len(loaded_images)} 张图片")

    # Spine 和 TOC
    spine = ["nav"]
    front_toc = []       # 前页 TOC 条目（扁平）
    body_spine_info = [] # 正文：记录 (group_idx, type, epub_obj) 用于构建嵌套 TOC
    back_toc = []        # 后页 TOC 条目（扁平）
    chapter_idx = 0

    # ── 前页 ──
    for i, fm in enumerate(structure.get("front_matter", [])):
        if not fm.get("keep", False):
            continue
        fm_html = _render_pages_html(
            fm.get("page_start", 0),
            fm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct, translations,
        )
        if not fm_html.strip():
            continue

        fm_label = fm.get("label", fm.get("type", "front"))
        chapter = epub.EpubHtml(
            title=fm_label,
            file_name=f"front_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{fm_label}</h1>\n" + promote_lone_display_math(fm_html)
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        front_toc.append(epub.Link(chapter.file_name, fm_label, f"fm_{i}"))

    # ── 正文（Popo 引擎：线性渲染标注 blocks） ──
    if structure.get("engine") in ("popo", "hybrid"):
        units = []
        if popo_blocks:
            units = _render_popo_body(
                popo_blocks, content_list, popo_body[0], popo_body[1],
                chinese_punct, title,
                toc_entries=structure.get("toc_entries"),
                translations=translations,
            )
            logger.info(
                f"  Popo 渲染: {len(units)} 个单元 "
                f"({sum(1 for u in units if u['kind'] == 'divider')} 编, "
                f"{sum(1 for u in units if u['kind'] == 'chapter')} 章)"
            )
        bi, chapter_idx = _emit_popo_body(
            units, book, spine, css, chapter_idx, meta.get("language", "zh")
        )
        body_spine_info.extend(bi)

    # ── 正文（层级嵌套，旧引擎；Popo 引擎时 spine_groups 为空，自动跳过） ──
    for group in spine_groups:
        parent = group.get("parent")
        children = group["children"]

        group_info = {"parent_link": None, "children_links": []}

        # 父级分隔页（编）
        if parent:
            divider_parts = [
                f'<h1 class="part-title">{_mathmlify(_convert_latex_sup(parent["title"]))}</h1>'
            ]
            # 渲染编的引言页（编起始 → 第一个章起始之前）
            if children:
                intro_end = children[0]["item"]["page_start"] - 1
                if parent["page_start"] <= intro_end:
                    intro_html = _render_pages_html(
                        parent["page_start"], intro_end,
                        pages, images_dir, noise_pages, chinese_punct, translations,
                    )
                    if intro_html.strip():
                        divider_parts.append(intro_html)

            divider = epub.EpubHtml(
                title=parent["title"],
                file_name=f"part_{chapter_idx:03d}.xhtml",
                lang="zh",
            )
            divider.content = "\n".join(divider_parts)
            divider.add_item(css)
            book.add_item(divider)
            spine.append(divider)
            group_info["parent_link"] = epub.Link(
                divider.file_name, parent["title"], f"part_{chapter_idx}"
            )
            chapter_idx += 1

        # 每个章（spine item）
        for child in children:
            ch_item = child["item"]
            sub_headings = child["sub_headings"]

            ch_html = _render_chapter_html(
                ch_item, sub_headings, pages, images_dir,
                noise_pages, chinese_punct,
                complete_outline=structure.get("complete_outline", []),
            )

            file_name = f"chapter_{chapter_idx:03d}.xhtml"
            chapter = epub.EpubHtml(
                title=ch_item["title"], file_name=file_name, lang="zh"
            )
            chapter.content = promote_lone_display_math(ch_html)
            chapter.add_item(css)
            book.add_item(chapter)
            spine.append(chapter)
            group_info["children_links"].append(
                epub.Link(file_name, ch_item["title"], f"ch_{chapter_idx}")
            )
            chapter_idx += 1

        body_spine_info.append(group_info)

    # ── 后页 ──
    for i, bm in enumerate(structure.get("back_matter", [])):
        bm_html = _render_pages_html(
            bm.get("page_start", 0),
            bm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct, translations,
        )
        if not bm_html.strip():
            continue

        bm_label = bm.get("label", bm.get("type", "back"))
        chapter = epub.EpubHtml(
            title=bm_label,
            file_name=f"back_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{bm_label}</h1>\n" + promote_lone_display_math(bm_html)
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        back_toc.append(
            epub.Link(chapter.file_name, bm_label, f"bm_{i}")
        )

    # ── 构建嵌套 TOC ──
    toc_entries = list(front_toc)
    for group_info in body_spine_info:
        parent_link = group_info["parent_link"]
        children = group_info["children_links"]
        if parent_link and children:
            # 有父级（编）→ 嵌套：Section(编标题, (章链接...))，编本身可点击
            toc_entries.append((
                epub.Section(parent_link.title, href=parent_link.href),
                tuple(children),
            ))
        elif parent_link:
            # 有父级但无子章 → 单独添加
            toc_entries.append(parent_link)
        else:
            # 无父级 → 扁平添加所有章
            toc_entries.extend(children)
    toc_entries.extend(back_toc)

    # ── 封面页 ──
    if cover_image_path:
        with open(cover_image_path, "rb") as f:
            cover_data = f.read()
        # 注册封面元数据（阅读器据此显示封面；原先手动 EpubItem 缺元数据）
        book.set_cover("images/cover.jpg", cover_data, create_page=False)
        # 添加封面页
        cover_html = epub.EpubHtml(
            title="封面",
            file_name="cover.xhtml",
            lang="zh",
        )
        cover_html.content = (
            '<html><body style="text-align:center;margin:0;padding:0;">'
            '<img src="images/cover.jpg" style="max-width:100%;max-height:100%;"/>'
            '</body></html>'
        )
        cover_html.add_item(css)
        book.add_item(cover_html)
        # 插入到 spine 最前面（在 "nav" 之后）
        spine.insert(1, cover_html)
        # 封面加入 TOC 最前面
        front_toc.insert(0, epub.Link("cover.xhtml", "封面", "cover"))

    # ── 组装 ──
    book.toc = tuple(toc_entries)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub_path = Path(output_dir) / f"{_sanitize_filename(title)}.epub"
    epub.write_epub(str(epub_path), book)

    logger.info(
        f"  EPUB 已生成: {epub_path} "
        f"({epub_path.stat().st_size / 1024:.0f} KB, "
        f"{chapter_idx} 个章节)"
    )
    return epub_path
