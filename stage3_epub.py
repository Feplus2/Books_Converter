"""
Stage 3: EPUB 生成

核心设计：层级嵌套渲染，杜绝内容重复。

- 编（level 1）→ 分隔页（part divider）
- 章（level 2）→ 独立 EPUB 章节（spine item）
- 节/小节（level 3-5）→ 内嵌在章内的子标题

每页内容只渲染一次，子标题在 block 流中按位置插入。
MinerU 的 text_level 不可靠——所有标题由 DeepSeek 结构分析提供。
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from ebooklib import epub
from PIL import Image

logger = logging.getLogger(__name__)

# ── 标点与正则常量 ──────────────────────────────────────────────

_SENTENCE_END = re.compile(r'[。！？…～;:。」』）\)】""\'?!]\s*$')
_BROKEN_P = re.compile(
    r'<p>([^<]*?)</p>\s*\n?\s*<p>([^<]*?)</p>',
    re.DOTALL,
)

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


def _convert_punctuation(text: str) -> str:
    """将英文标点转为中文标点（仅用于含中文的文本）"""
    if not re.search(r'[\u4e00-\u9fff]', text):
        return text
    text = text.translate(_PUNCT_MAP)
    # 句号：避免误转数字中的点（如 3.14）
    text = re.sub(r'(?<!\d)\.(?!\d)', '。', text)
    return text


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
    width: 100%;
    margin: 1em 0;
}
table td, table th {
    border: 1px solid #ccc;
    padding: 4px 8px;
}
blockquote {
    margin: 1em 2em;
    color: #555;
}
"""


# ── 内容分类 ────────────────────────────────────────────────────

def _classify_blocks_by_page(content_list: list) -> dict:
    """将 content_list 按页码分组（1-based 页码）"""
    pages = {}
    for block in content_list:
        page = block.get("page_idx", 0) + 1
        if page not in pages:
            pages[page] = []
        pages[page].append(block)
    return pages


# ── Block → HTML 渲染 ──────────────────────────────────────────

def _render_block_to_html(block: dict, images_dir: str,
                          chinese_punct: bool = False) -> str:
    """将单个 content block 渲染为 HTML。

    注意：标题类 block（type='title' 或 text_level>=1）应已由
    DeepSeek 结构分析处理，此处仅渲染正文/图片/表格等非标题内容。
    """
    btype = block.get("type", "")
    text = block.get("text", "")

    if btype in ("text", "paragraph"):
        if not text.strip():
            return ""
        level = block.get("text_level", 0)
        # level >= 1 的文本块可能是 MinerU 猜的标题
        # 调用方会预先过滤，但这里也做兜底：
        if level >= 1:
            return ""  # 跳过，由 DeepSeek 标题覆盖
        if chinese_punct:
            text = _convert_punctuation(text)
        return f"<p>{text}</p>"

    elif btype == "title":
        # DeepSeek 提供权威标题，MinerU 的 title 块一律跳过
        return ""

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
            html += f'<p class="no_indent"><small>{caption}</small></p>'
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
            html += f'<p class="no_indent"><strong>{caption}</strong></p>'
        html += body
        return html

    elif btype == "list":
        items = block.get("list_items", [])
        if not items:
            return ""
        items_html = "\n".join(f"<li>{item}</li>" for item in items)
        return f"<ul>{items_html}</ul>"

    elif btype == "equation":
        latex = block.get("latex", "") or block.get("text", "")
        return f'<p class="no_indent"><code>{latex}</code></p>'

    elif btype == "code":
        code = block.get("code_body", "") or block.get("text", "")
        lang = block.get("code_language", "")
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f"<pre><code{lang_attr}>{code}</code></pre>"

    elif btype in ("header", "footer", "page_number", "page_footnote",
                    "aside_text"):
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


# ── 子标题模式检测 ──────────────────────────────────────────────

# 中文子标题模式
_CN_SUB_L4 = re.compile(
    r'^[一二三四五六七八九十百]+、'  # 一、 二、 三、...
)
_CN_SUB_L5 = re.compile(
    r'^（[一二三四五六七八九十百]+）'  # （一）（二）...
)
# 数字子标题模式
_NUM_DOT = re.compile(r'^\d+[\.、]')      # 1. 2. 或 1、2、
_NUM_PAREN = re.compile(r'^\(\d+\)')      # (1) (2)
_NUM_CN_PAREN = re.compile(r'^（\d+）')   # （1）（2）


def _detect_sub_heading_level(text: str) -> int:
    """检测文本是否为常见子标题格式，返回建议的 heading level。

    返回 4（一、二、）或 5（（一）（二）/ 1. 2. / (1) (2)），
    不匹配则返回 0。
    """
    t = text.strip()
    if not t or len(t) > 80:
        return 0
    # 不能以句末标点结尾（否则是完整句子/问题，不是标题）
    if _SENTENCE_END.search(t):
        return 0
    # 文本中间包含问号 → 可能是思考题，跳过
    if '？' in t or '?' in t:
        return 0

    if _CN_SUB_L4.match(t):
        return 4
    if _CN_SUB_L5.match(t):
        return 5
    # 数字子标题：要求更短（< 50 字符），避免把带编号的长段落当标题
    if len(t) < 50:
        if _NUM_DOT.match(t) or _NUM_PAREN.match(t) or _NUM_CN_PAREN.match(t):
            return 5
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


# ── 章节渲染（核心：按页渲染 + 标题覆写） ──────────────────────

def _render_chapter_html(
    ch_item: dict,
    sub_headings: list,
    pages: dict,
    images_dir: str,
    noise_pages: set,
    chinese_punct: bool,
) -> str:
    """渲染一个章（spine item）的完整 HTML。

    策略：
    1. 以章标题开头（h2）
    2. 逐页渲染 content blocks
    3. 遇到匹配 DeepSeek 子标题的 block → 替换为正确层级的 h-tag
    4. 跳过 MinerU 的 title 块和已知标题文本（避免重复）
    """
    ch_level = ch_item.get("level", 2)
    ch_tag = f"h{min(ch_level, 2)}"
    parts = [f'<{ch_tag} class="chapter-title">{ch_item["title"]}</{ch_tag}>']

    # 构建标题查找表（归一化文本 → 子标题信息）
    heading_lookup = {}
    for sub in sub_headings:
        key = _normalize(sub["title"])
        heading_lookup[key] = sub

    # 需要跳过的文本集合（章标题 + 所有子标题）
    skip_texts = set(heading_lookup.keys())
    skip_texts.add(_normalize(ch_item["title"]))

    # 逐页渲染
    for page in range(ch_item["page_start"], ch_item["page_end"] + 1):
        if page in noise_pages:
            continue

        for block in pages.get(page, []):
            text = block.get("text", "")
            btype = block.get("type", "")

            # 1. 跳过所有 title 类型（MinerU 猜的标题）
            if btype == "title":
                continue

            # 2. 检查是否匹配已知子标题 → 插入正确层级的 h-tag
            normalized = _normalize(text) if text else ""
            if normalized and normalized in heading_lookup:
                sub = heading_lookup[normalized]
                htag = f"h{min(sub['level'], 6)}"
                parts.append(f"<{htag}>{sub['title']}</{htag}>")
                continue

            # 3. 跳过匹配章标题的 block
            if normalized and normalized in skip_texts:
                continue

            # 4. 处理 MinerU 猜的标题块（text_level >= 1）
            if btype == "text" and block.get("text_level", 0) >= 1:
                t = text.strip()
                if t and len(t) < 80 and not _SENTENCE_END.search(t):
                    # 不在 DeepSeek 标题列表中，但可能是子标题
                    # 尝试模式匹配：一、二、→ h4，（一）（二）→ h5
                    detected_level = _detect_sub_heading_level(t)
                    if detected_level:
                        htag = f"h{detected_level}"
                        parts.append(f"<{htag}>{t}</{htag}>")
                    # 不匹配任何模式 → 跳过（避免重复的伪标题）
                    continue

            # 5. 正常渲染
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
                text = block.get("text", "")
                if text.strip():
                    level = block.get("text_level", 0) or 1
                    html_parts.append(f"<h{min(level + 1, 4)}>{text}</h{min(level + 1, 4)}>")
                continue
            rendered = _render_block_to_html(block, images_dir, chinese_punct)
            if rendered:
                html_parts.append(rendered)
    return "\n".join(html_parts)


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


# ── 主函数 ──────────────────────────────────────────────────────

def generate_epub(
    book_name: str,
    mineru_info: dict,
    structure: dict,
    output_dir: str,
) -> Path:
    """
    生成 EPUB 文件。

    Args:
        book_name: 书名
        mineru_info: Stage 1 输出 {markdown, content_list, images_dir}
        structure: Stage 2 输出 {metadata, front_matter, body, back_matter, noise_ranges}
        output_dir: 输出目录
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

    # 确定 spine 层级和分组
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
    toc_entries = []
    chapter_idx = 0

    # ── 前页 ──
    for i, fm in enumerate(structure.get("front_matter", [])):
        if not fm.get("keep", False):
            continue
        fm_html = _render_pages_html(
            fm.get("page_start", 0),
            fm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct,
        )
        if not fm_html.strip():
            continue

        fm_label = fm.get("label", fm.get("type", "front"))
        chapter = epub.EpubHtml(
            title=fm_label,
            file_name=f"front_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{fm_label}</h1>\n{fm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        toc_entries.append(epub.Link(chapter.file_name, fm_label, f"fm_{i}"))

    # ── 正文（层级嵌套） ──
    for group in spine_groups:
        parent = group.get("parent")
        children = group["children"]

        # 父级分隔页（编）
        if parent:
            divider_parts = [
                f'<h1 class="part-title">{parent["title"]}</h1>'
            ]
            # 渲染编的引言页（编起始 → 第一个章起始之前）
            if children:
                intro_end = children[0]["item"]["page_start"] - 1
                if parent["page_start"] <= intro_end:
                    intro_html = _render_pages_html(
                        parent["page_start"], intro_end,
                        pages, images_dir, noise_pages, chinese_punct,
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
            toc_entries.append(
                epub.Link(divider.file_name, parent["title"],
                          f"part_{chapter_idx}")
            )
            chapter_idx += 1

        # 每个章（spine item）
        for child in children:
            ch_item = child["item"]
            sub_headings = child["sub_headings"]

            ch_html = _render_chapter_html(
                ch_item, sub_headings, pages, images_dir,
                noise_pages, chinese_punct,
            )

            file_name = f"chapter_{chapter_idx:03d}.xhtml"
            chapter = epub.EpubHtml(
                title=ch_item["title"], file_name=file_name, lang="zh"
            )
            chapter.content = ch_html
            chapter.add_item(css)
            book.add_item(chapter)
            spine.append(chapter)
            toc_entries.append(
                epub.Link(file_name, ch_item["title"], f"ch_{chapter_idx}")
            )
            chapter_idx += 1

    # ── 后页 ──
    for i, bm in enumerate(structure.get("back_matter", [])):
        bm_html = _render_pages_html(
            bm.get("page_start", 0),
            bm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct,
        )
        if not bm_html.strip():
            continue

        bm_label = bm.get("label", bm.get("type", "back"))
        chapter = epub.EpubHtml(
            title=bm_label,
            file_name=f"back_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{bm_label}</h1>\n{bm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        toc_entries.append(
            epub.Link(chapter.file_name, bm_label, f"bm_{i}")
        )

    # ── 组装 ──
    book.toc = toc_entries
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
