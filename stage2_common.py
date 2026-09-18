"""
Stage 2 共享收尾：轻量兜底 / 目录页检测 / TOC 锚点+形状栈校正 / 重页丢弃

popo 与 hybrid 等结构引擎仅在"如何得到标注 blocks"上不同，此后流程
完全一致，统一在这里收尾：

1. 重页检测丢弃（源 PDF 同一页被扫描两次）
2. DeepSeek 轻量兜底：一次调用拿 metadata + 前页/后页分类 + 目录条目
3. 目录页密度检测，修正 front_matter 的 toc 边界（长目录防漏）
4. TOC 锚定校正：用目录条目（绝对真值）+ 形状栈（通用编号先验）
   校准模型的漂移层级
5. 标注 blocks → 文档树
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

from openai import OpenAI

import popo
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    GLOBAL_LEVEL_PASS,
)
from llm_thinking import chat_create

logger = logging.getLogger(__name__)

# DeepSeek 轻量兜底采样参数
_FRONT_PAGES = 15   # 书首采样页数
_BACK_PAGES = 5     # 书尾采样页数
_PAGE_CHARS = 4000  # 每页最多采样字符（目录页常超 1000 字：病例 Feeling Great
                    # 两页目录 1263/953 字符被 800 截断 → 11-19 章条目丢失）

_LIGHT_PROMPT = """你是一位图书结构分析师。以下是一本书【开头 {front} 页】和【结尾 {back} 页】的文本采样，页码用 [P{{N}}] 标记（N 为扫描页码，从 1 开始）。

另外，附上一份【全书标题列表】（由结构模型检测，含页码），供你判断边界时参考。

请判断这本书的元数据和前后页结构，输出 JSON：
{{
  "metadata": {{
    "title": "书名（以标题页为准，不带出版信息）",
    "authors": ["作者"],
    "translator": "译者（无则 null）",
    "publisher": "出版社（无则 null）",
    "language": "主要语言代码，如 zh/en/de/ja/fr"
  }},
  "front_matter": [
    {{"type": "cover|copyright|dedication|toc|preface|foreword|introduction", "label": "简短描述", "page_start": N, "page_end": N, "keep": true}}
  ],
  "back_matter": [
    {{"type": "appendix|bibliography|index|afterword|colophon|notes", "label": "简短描述", "page_start": N, "page_end": N}}
  ],
  "toc_entries": [
    {{"text": "目录条目的完整文字（如'第一章 蠢材的天堂'，不含页码和点线）", "level": 1, "page": 12}}
  ]
}}

规则：
1. front_matter 覆盖第 1 页到正文开始前的所有页，连续、不重叠
2. 封面(cover)和目录(toc)的 keep=false，其余 keep=true
3. 正文从最后一项 front_matter 的 page_end + 1 页开始
4. back_matter 是正文结束后的部分（附录/参考文献/索引/后记/批注/致谢等）。
   **其 page_start 必须以【全书标题列表】中实际存在的对应标题页码为准**，
   不要凭采样猜测；列表中找不到依据的条目不要输出
5. toc_entries 从目录页文本中提取，**覆盖目录出现的所有层级**（编/篇/卷、章、节，
   通常 2-3 层），level 从 1 开始递增；保持目录中的原始完整文字
   （OCR 可能有少量错字，选择最合理的版本）；page 填该条目在目录中标注的
   印刷页码（整数），条目本身不带页码则填 null。
   **若采样页中不存在目录页，toc_entries 必须输出 []**——严禁根据
   【全书标题列表】编造、推测或拼凑目录
6. 所有页码用整数，language 用两位小写代码
只输出 JSON，不要输出任何解释。

=== 以下是文本采样 ===

{sample}

=== 以下是全书标题列表 ===

{titles}"""


_LIGHT_TOC_PROMPT = """以下是一本书【开头】若干页的文本采样，页码用 [P{{N}}] 标记（N 为扫描页码，从 1 开始）。

请从目录页文本中提取全部目录条目，输出 JSON 数组。每项格式：
["条目完整文字（不含页码和点线）", 层级, 印刷页码]
- 覆盖目录出现的所有层级（编/篇/卷、章、节，通常 2-3 层），level 从 1 开始递增
- 保持目录中的原始完整文字（OCR 可能有少量错字，选择最合理的版本）
- 印刷页码填整数，条目本身不带页码则填 null
- 若采样页中不存在目录页，必须输出 []，不要编造或推测目录
只输出 JSON 数组，不要输出任何解释。

=== 以下是文本采样 ===

{sample}"""


_LIGHT_META_PROMPT = """你是一位图书结构分析师。以下是一本书【开头 {front} 页】和【结尾 {back} 页】的文本采样，页码用 [P{{N}}] 标记（N 为扫描页码，从 1 开始）。

另外，附上一份【全书标题列表】（由结构模型检测，含页码），供你判断边界时参考。

请判断这本书的元数据和前后页结构，输出 JSON：
{{
  "metadata": {{
    "title": "书名（以标题页为准，不带出版信息）",
    "authors": ["作者"],
    "translator": "译者（无则 null）",
    "publisher": "出版社（无则 null）",
    "language": "主要语言代码，如 zh/en/de/ja/fr"
  }},
  "front_matter": [
    {{"type": "cover|copyright|dedication|toc|preface|foreword|introduction", "label": "简短描述", "page_start": N, "page_end": N, "keep": true}}
  ],
  "back_matter": [
    {{"type": "appendix|bibliography|index|afterword|colophon|notes", "label": "简短描述", "page_start": N, "page_end": N}}
  ]
}}

规则：
1. front_matter 覆盖第 1 页到正文开始前的所有页，连续、不重叠
2. 封面(cover)和目录(toc)的 keep=false，其余 keep=true
3. 正文从最后一项 front_matter 的 page_end + 1 页开始
4. back_matter 是正文结束后的部分（附录/参考文献/索引/后记/批注/致谢等）。
   **其 page_start 必须以【全书标题列表】中实际存在的对应标题页码为准**，
   不要凭采样猜测；列表中找不到依据的条目不要输出
5. 所有页码用整数，language 用两位小写代码
只输出 JSON，不要输出任何解释。

=== 以下是文本采样 ===

{sample}

=== 以下是全书标题列表 ===

{titles}"""


def save_structure(structure: dict, output_dir: str) -> Path:
    path = Path(output_dir) / "structure.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)
    logger.info(f"  结构已保存: {path}")
    return path


def _page_texts(content_list: list) -> dict:
    """page_idx+1 → 该页拼接文本（截断到 _PAGE_CHARS 字符）"""
    pages = {}
    for block in content_list:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        page = block.get("page_idx", 0) + 1
        pages.setdefault(page, []).append(text)
    return {p: "\n".join(t)[:_PAGE_CHARS] for p, t in pages.items()}


# 目录页标题词（简/繁/间隔号/英文；归一化后精确全等才算，防正文提及误捕）
_TOC_HEADING_WORDS = {"目录", "目錄", "contents", "content"}


def _find_toc_page(pages: dict) -> int | None:
    """本地探测目录页（1 起页码）：页内存在独立的目录标题短行即算。
    只看前 60 页（目录几乎不可能更靠后；拿不准返回 None——不动作）。"""
    for p in sorted(pages):
        if p > 60:
            break
        if p < 3:
            continue  # 封面/扉页不谈目录
        for line in pages[p].split("\n"):
            norm = re.sub(r"[\s　]+", "", line).strip().casefold()
            if norm in _TOC_HEADING_WORDS:
                return p
    return None


# ── 规则目录兜底提取（病例 029/030）──────────────────────────────
# LLM 提取失败的两条现实路径：目录页被推荐序等前页推出采样窗（八次危机，
# 扩窗已先行拦截）；端点内容过滤整单拒答（中國36問，GLM 1301——政治书目
# 的目录条目本身就是敏感词表）。两路的共同点：目录页就在那儿、形态清晰。
# 规则提取器只在 LLM 交付 0 条且本地探到目录页时启动——失败方向不动作
# （提取为空则维持现状，与无目录书同路径）。

# 条目尾：文字 + 点线/空白 + 1-4 位页码（'推荐序 001'、'一、从外资… 010'）
_TOC_ENTRY_TAIL_RE = re.compile(r"^(.+?)[\s.·…．]*\s(\d{1,4})\s*$")
# 作者行形态：'裴宜理 (Elizabeth J. Perry) 13'——页码属上一行条目（中國36問）
_TOC_AUTHOR_TAIL_RE = re.compile(r"^[一-鿿·]{2,5}\s*[(（][A-Za-z]")
# 行内作者剥除：'导论 宋怡明 (Michael Szonyi) 3' → '导论'
_TOC_AUTHOR_INLINE_RE = re.compile(r"\s*[一-鿿·]{2,5}\s*[(（][A-Za-z].*$")
# 待配对行的标题形状（'第X…'/'N.'/ '一、'/'（一）'/序导言类）
_TOC_PENDING_RE = re.compile(
    r"^(第\s*[一二三四五六七八九十百零〇0-9]+|\d{1,2}\s*[.、．]|[一二三四五六七八九十]+、"
    r"|[（(][一二三四五六七八九十]+[）)]|导论|导言|引言|序|前言|后记|跋|附录)")


def _rule_toc_level(text: str, has_chap: bool) -> int:
    """按编号形状定级（形状栈后续还会校正相对深度，这里只给首票）"""
    t = re.sub(r"\s+", "", text)
    if re.match(r"^第[一二三四五六七八九十百零〇0-9]+(部分|[编篇卷部])", t):
        return 1
    if re.match(r"^(第)?[一二三四五六七八九十百零〇0-9]+章", t):
        return 2
    if re.match(r"^(推荐序|自序|序言|前言|序|导论|导言|引言|后记|跋|附录)", t):
        return 1
    if re.match(r"^\d{1,2}[.、．]", t):
        return 3 if has_chap else 2
    if re.match(r"^[一二三四五六七八九十]+、", t):
        return 3
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]", t):
        return 4
    return 2


def _rule_toc_extract(pages: dict, toc_page: int) -> list:
    """从目录页区间规则提取条目。返回 toc_entries 同构列表（可能为空）。

    区间：toc_page 起，连续有 ≥2 条目的页都收（目录跨页），一页 0 条即停；
    全书上限 400 条（防失控）。无页码的部分/编/章/序类结构词条目以
    page=None 收录（文本锚定不需要页码；'第 一 部 分 政 治' 这类间隔号
    标题在目录里常不带页码）。
    """
    # 无页码也收的结构词形状（部分/编/章/序类；'N.'/'一、' 无页码不收——
    # 可能是正文编号）
    _struct_word = re.compile(
        r"^(第\s*[一二三四五六七八九十百零〇0-9]+\s*(部分|[编篇卷部章])"
        r"|推荐序|自序|序言|前言|序|导论|导言|引言|后记|跋|附录)")
    entries = []
    seen = set()

    def _emit(head: str, page_no):
        head = _TOC_AUTHOR_INLINE_RE.sub("", head).strip()
        head = re.sub(r"[\s.·…．]+$", "", head).strip()
        if len(re.sub(r"[\s　]+", "", head)) < 2 or len(head) > 60 \
                or re.fullmatch(r"\d+", head):
            return  # 太短/纯数字/超长段落都不是条目（介绍性段落混入目录页）
        key = (_normalize_title(head), page_no)
        if key in seen:
            return
        seen.add(key)
        entries.append({"text": head, "level": 0, "page": page_no})

    for p in range(toc_page, toc_page + 12):
        if p not in pages:
            if entries:
                break
            continue
        page_entries_before = len(entries)
        pending = None
        for raw_line in pages[p].split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            norm = re.sub(r"[\s　]+", "", line).casefold()
            if norm in _TOC_HEADING_WORDS or re.fullmatch(r"[0-9ivxlcdm]+", norm):
                continue  # 目录标题行/页码行
            m = _TOC_ENTRY_TAIL_RE.match(line)
            if m and _TOC_AUTHOR_TAIL_RE.match(m.group(1)) and pending:
                _emit(pending, int(m.group(2)))   # 作者行带页码：归上一行条目
                pending = None
            elif m:
                if pending and _struct_word.match(pending):
                    _emit(pending, None)          # 结构词条目无页码也收
                _emit(m.group(1), int(m.group(2)))
                pending = None
            elif _TOC_PENDING_RE.match(line):
                if pending and _struct_word.match(pending):
                    _emit(pending, None)
                pending = line      # 无页码的条目前半行，等下一行的页码
            else:
                if pending and _struct_word.match(pending):
                    _emit(pending, None)
                pending = None
            if len(entries) >= 400:
                break
        if pending and _struct_word.match(pending):
            _emit(pending, None)
        if len(entries) == page_entries_before and entries:
            break  # 一页 0 条 = 目录区间结束
    has_chap = any(re.match(r"^第?\s*[一二三四五六七八九十百零〇0-9]+\s*章",
                            re.sub(r"\s+", "", e["text"])) for e in entries)
    for e in entries:
        e["level"] = _rule_toc_level(e["text"], has_chap)
    return entries


def _clean_json_response(raw: str) -> str:
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError(f"无法从响应中提取JSON: {raw[:300]}...")


def _clean_json_array_response(raw: str) -> str:
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError(f"无法从响应中提取JSON数组: {raw[:300]}...")


def _fallback_metadata(book_name: str) -> dict:
    return {
        "metadata": {"title": book_name, "authors": [], "translator": None,
                     "publisher": None, "language": "zh"},
        "front_matter": [],
        "back_matter": [],
        "toc_entries": [],
    }


def _light_metadata_pass(content_list: list, book_name: str,
                         popo_titles: list | None = None, progress=None) -> dict:
    """一次轻量 DeepSeek 调用：metadata + 前页/后页分类 + 目录条目。

    只采样书首 _FRONT_PAGES 页和书尾 _BACK_PAGES 页（每页截断），
    全书结构（标题层级）由结构引擎负责，这里不碰。
    popo_titles: 结构模型检测的 [(page, level, text), ...]，辅助判断后页边界。
    失败时降级为 _fallback_metadata（front/back 为空，正文=全书）。
    """
    _report = progress or (lambda *a, **kw: None)
    pages = _page_texts(content_list)
    if not pages:
        return _fallback_metadata(book_name)

    max_page = max(pages)
    front = sorted(p for p in pages if p <= _FRONT_PAGES)
    # 采样扩窗（病例 029）：推荐序/自序/概念提示把目录推出前 15 页时
    # （八次危机目录在 P23-31），LLM 按规则只能输出 []——本地探测到目录页
    # 超出采样窗就扩窗覆盖（目录页及其后 8 页；目录可能跨页）。失败方向：
    # 探测不到目录页则维持原窗，与旧行为一致。
    toc_page = _find_toc_page(pages)
    if toc_page is not None and toc_page + 8 > _FRONT_PAGES:
        extended = sorted(p for p in pages if p <= toc_page + 8)
        if len(extended) > len(front):
            logger.info(f"  目录页在 P{toc_page}，采样窗扩至 {toc_page + 8} 页")
            front = extended
    back = sorted(p for p in pages if p > max_page - _BACK_PAGES and p not in front)

    sample_parts = [f"[P{p}]\n{pages[p]}" for p in front]
    if back:
        sample_parts.append("\n（……中间正文略……）\n")
        sample_parts += [f"[P{p}]\n{pages[p]}" for p in back]
    sample = "\n\n".join(sample_parts)

    titles_text = "（无）"
    if popo_titles:
        titles_text = "\n".join(
            f"[P{p}] L{lv} {t}" for p, lv, t in popo_titles
        )

    _report(f"DeepSeek 轻量兜底: metadata + 前后页分类 ({len(sample):,} 字符)...")
    logger.info(f"  DeepSeek 轻量兜底: 采样 {len(front)}+{len(back)} 页, {len(sample):,} 字符")

    def _call(prompt: str) -> str:
        resp = chat_create(
            client,
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.1,
        )
        return resp.choices[0].message.content

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    try:
        raw = _call(_LIGHT_PROMPT.format(
            front=len(front), back=len(back), sample=sample,
            titles=titles_text))
        result = json.loads(_clean_json_response(raw))
    except Exception as e:
        # 目录大的书（数百条目）响应可能超出 max_tokens 被截断 → JSON 残缺。
        # 拆成两个紧凑调用重试（各自响应都小）：metadata/前后页 + 目录条目
        logger.warning(f"  轻量兜底首次失败: {e}，拆分紧凑重试")
        meta_part = None
        toc_entries = []
        try:
            raw_m = _call(_LIGHT_META_PROMPT.format(
                front=len(front), back=len(back), sample=sample,
                titles=titles_text))
            meta_part = json.loads(_clean_json_response(raw_m))
        except Exception as e2:
            logger.warning(f"  metadata 紧凑重试失败: {e2}")
        try:
            front_sample = "\n\n".join(f"[P{p}]\n{pages[p]}" for p in front)
            raw_t = _call(_LIGHT_TOC_PROMPT.format(sample=front_sample))
            toc_entries = _parse_toc_array(
                json.loads(_clean_json_array_response(raw_t)))
        except Exception as e2:
            logger.warning(f"  目录紧凑重试失败: {e2}")
        if meta_part is None and not toc_entries:
            logger.warning("  轻量兜底全部失败，使用降级 metadata")
            return _fallback_metadata(book_name)
        result = meta_part or _fallback_metadata(book_name)
        result["toc_entries"] = toc_entries
        if toc_entries:
            logger.info(f"    紧凑重试挽回目录条目 {len(toc_entries)} 条")

    # 校验/补全（LLM 可能把某字段输出成 null，setdefault 挡不住 None → or 防御）
    result["metadata"] = result.get("metadata") or {}
    result["metadata"].setdefault("title", book_name)
    result["metadata"].setdefault("language", "zh")
    result["front_matter"] = result.get("front_matter") or []
    result["back_matter"] = result.get("back_matter") or []
    result["toc_entries"] = result.get("toc_entries") or []
    for entry in result["front_matter"] + result["back_matter"]:
        for f in ("page_start", "page_end"):
            try:
                entry[f] = int(entry.get(f, 0))
            except (TypeError, ValueError):
                entry[f] = 0
        entry.setdefault("keep", True)
        entry.setdefault("label", entry.get("type", ""))

    logger.info(
        f"    完成: 前页 {len(result['front_matter'])} 项, "
        f"后页 {len(result['back_matter'])} 项, "
        f"目录条目 {len(result['toc_entries'])}, "
        f"语言 {result['metadata'].get('language')}"
    )
    return result


def _parse_toc_array(data) -> list:
    """紧凑目录数组 [[text, level, page], ...] → toc_entries 字典列表（容错）。"""
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        try:
            text = str(item[0]).strip()
            level = int(item[1])
            page = item[2] if len(item) > 2 else None
            if text and level > 0:
                out.append({"text": text, "level": level,
                            "page": int(page) if page is not None else None})
        except (TypeError, ValueError, IndexError):
            continue
    return out


# 标题里的上标脚注标记（'人名索引 $^{①}$'）与法式装饰前缀（'— X. —'），
# 匹配前剥离（显示文本保留原文，只影响归一化键）
_SUP_MARK_RE = re.compile(r"\$\^\{[^{}]*\}\$")
_DECOR_PREFIX_RE = re.compile(r"^[—–-]\s*[IVXLCDM]+\.?\s*[—–-]\s*", re.I)

# unicode 上下标 → ASCII（标题匹配用归一，不动原文）：'Yₓ'/'Y_{X}'/'Yx' 归一
_UNICODE_SUB_SUPER_MAP = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
    "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
    "₌": "=", "₍": "(", "₎": ")", "ₐ": "a", "ₑ": "e", "ₒ": "o",
    "ₓ": "x", "ₔ": "e", "ₕ": "h", "ₖ": "k", "ₗ": "l", "ₘ": "m",
    "ₙ": "n", "ₚ": "p", "ₛ": "s", "ₜ": "t",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "ⁿ": "n", "ⁱ": "i",
})

# LaTeX 命令 → Unicode（病例 024，锚点归一化用；长的在前防前缀截胡）
_LATEX_SYMBOL_MAP = (
    ("\\longrightarrow", "→"), ("\\rightarrow", "→"), ("\\leftarrow", "←"),
    ("\\Rightarrow", "⇒"), ("\\times", "×"), ("\\cdot", "·"),
    ("\\pm", "±"), ("\\mp", "∓"), ("\\to", "→"), ("\\infty", "∞"),
    ("\\alpha", "α"), ("\\beta", "β"), ("\\gamma", "γ"), ("\\delta", "δ"),
    ("\\epsilon", "ε"), ("\\zeta", "ζ"), ("\\eta", "η"), ("\\theta", "θ"),
    ("\\lambda", "λ"), ("\\mu", "μ"), ("\\nu", "ν"), ("\\xi", "ξ"),
    ("\\pi", "π"), ("\\rho", "ρ"), ("\\sigma", "σ"), ("\\tau", "τ"),
    ("\\phi", "φ"), ("\\chi", "χ"), ("\\psi", "ψ"), ("\\omega", "ω"),
    ("\\Lambda", "Λ"), ("\\Sigma", "Σ"), ("\\Phi", "Φ"), ("\\Omega", "Ω"),
)


def _normalize_title(text: str) -> str:
    """标题归一化：剥脚注上标/装饰前缀 + 去 $ 定界符和所有空白 + 大小写折叠，
    用于目录条目匹配。'$' 是数学定界符，目录与正文的公式块常差一层 $$
    包裹（'一、$f(x)=..$型' vs '$$ 一、f(x)=.. 型 $$'），剥掉才对齐。
    （'FOREWORD' 应能匹配 'Foreword: François Ewald …' 前缀；
    中文无大小写，不受影响）

    LaTeX 命令与上下标归一（病例 024）：目录 OCR 与正文公式形态系统性差异
    （'$W^{\\pm}$' ↔ 'W±'、'$SU(3)$' ↔ 'SU(3)'、'$K^{0}\\rightarrow\\pi^{-}$'
    的上标/命令在目录侧是 Unicode 字符）。两侧同归一，非公式文本不受影响。"""
    t = _SUP_MARK_RE.sub("", text or "")
    t = _DECOR_PREFIX_RE.sub("", t.strip())
    # HTML 标签剥除（病例 031：文字版 PDF 的文本层把冒号包成 <sub>：</sub>
    # 混进标题——'第二章 土耳其<sub>：</sub>地缘…' vs 目录 '第二章 土耳其：地缘…'）
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("$", "")
    # \mathrm/\mathbf 等格式包装只留内容（'Y_{\mathrm{X}}' ↔ 'Yₓ'，机械手册
    # 目录锚点落空实测；必须在通用命令剥除之前，否则剩 'mathrmX' 垃圾字母）
    t = re.sub(r"\\math[a-z]+\{([^{}]*)\}", r"\1", t)
    # unicode 上下标字符 → ASCII（目录侧 'Yₓ' ↔ 正文侧 'Yx'/'Y_{X}' 三形态归一）
    t = t.translate(_UNICODE_SUB_SUPER_MAP)
    # LaTeX 格式命令是纯排版噪声（目录与正文常不一致）
    t = t.replace("\\left", "").replace("\\right", "")
    for cmd, uni in _LATEX_SYMBOL_MAP:
        t = t.replace(cmd, uni)
    t = re.sub(r"\\([A-Za-z]+)", r"\1", t)  # 未收录命令保留字母主体
    # LaTeX 间距命令是纯排版噪声（目录 '\cos \omega x' ↔ 正文 '\cos\omega x\,'
    # 实测失配——高数 '二、e^{λx}[P_l(x)…] 型' 锚点落空）
    t = re.sub(r"\\[,;:! ]", "", t)
    t = re.sub(r"[{}]", "", t)               # 分组括号无上位含义
    t = t.replace("^", "").replace("_", "")  # 上下标标记（'w^{±}'→'w±'）
    # 弯引号/弯撇号统一为直引（OCR 与目录常不一致）
    t = (t.replace("’", "'").replace("‘", "'")
           .replace("“", '"').replace("”", '"'))
    # 全半角括号统一（病例 037：目录半角（义位系统）↔ 正文全角（义位系统）
    # 失配，锚点落空 → 救援合成幻影）
    t = t.replace("（", "(").replace("）", ")")
    # 编号句点归一（目录 '12. All-or-Nothing' ↔ 正文 '12  All-or-Nothing'、
    # 'III. The…' ↔ 'III The…'——印刷目录带句点、正文标题常无，FG 实测
    # 四个章因此锚不上；(?=\D) 防误伤小数形 '3.14'）
    t = re.sub(r"^(\d{1,3})[.、．](?=\D)", r"\1", t)
    t = re.sub(r"^([IVXLC]+)[.、．](?=\D)", r"\1", t)
    return re.sub(r"[\s　]+", "", t).strip().casefold()


# 通用编号形状（跨语言，按典型深度排序，仅供形状栈排名参考）
_SHAPE_PATTERNS = [
    ("part_cn", re.compile(r"^第[一二三四五六七八九十百零〇0-9]+[编篇卷部]")),
    ("part_en", re.compile(r"^(part|volume|book|teil|partie|tome)\b", re.I)),
    ("chap_cn", re.compile(r"^第[一二三四五六七八九十百零〇0-9]+章")),
    ("chap_en", re.compile(r"^(chapter|kapitel|chapitre)\b", re.I)),
    ("sec_cn", re.compile(r"^第[一二三四五六七八九十百零〇0-9]+节")),
    ("sec_en", re.compile(r"^(§|section)\b", re.I)),
    ("num_cn", re.compile(r"^[一二三四五六七八九十]+、")),
    ("num_cn_paren", re.compile(r"^[（(]?[一二三四五六七八九十]+[）)]")),
    ("num_dot", re.compile(r"^\d+\.\s*\S")),
    # 竖线变体：'12 | All-or-Nothing Thinking'（Feeling Great 章首版式；
    # 无此形状时按 plain 被无锚下沉机制误压——ch12 因此沉出 nav）
    ("num_bar", re.compile(r"^\d+\s*[|｜]\s*\S")),
    ("num_paren", re.compile(r"^[（(]?\d+[）)]")),
    ("roman", re.compile(r"^[IVXLCDM]+[.、]\s")),
    ("alpha", re.compile(r"^[a-zA-Z][.、]\s")),
]

# 'Problem 3.1.'/'Exercise 2'/'例 3' 类编号前缀（用于归一化键：已
# casefold、无空白）。印刷目录常省略该编号前缀，剥掉后块与条目对齐
# （病例 024：QFT 习题章 11 条节标题锚不上）。词表严格限定+必须带数字。
_PROBLEM_PREFIX_RE = re.compile(
    r"^(?:problem|exercise|example|aufgabe|probl[eè]me|exercice"
    r"|习题|例题|问题|练习|思考)\d+(?:[.、．]\d+)*[.、．]?"
)


def _title_shape(text: str) -> str:
    """标题的编号形状（无编号 → plain）"""
    t = (text or "").strip()
    for name, pat in _SHAPE_PATTERNS:
        if pat.match(t):
            return name
    return "plain"


def _edit_distance_le(a: str, b: str, limit: int) -> int:
    """有界 Levenshtein 距离：超过 limit 提前返回 limit+1。"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            row_min = min(row_min, v)
        if row_min > limit:
            return limit + 1
        prev = cur
    return prev[-1]


# 目录条目/行尾页码（点线、空格、斜杠、破折号引导）：'xxx …… 60'、'xxx / 060'
_TRAIL_PAGE_RE = re.compile(r"[\s.…·_/／—–]+\d+\s*$")


def _strip_trailing_page(text: str) -> str:
    return _TRAIL_PAGE_RE.sub("", (text or "").strip()).strip()


# 扫描本第三方自制书签的典型形态：'标题／页码'（全角／或半角 / + 印刷页码）
_JUNK_BOOKMARK_RE = re.compile(r"[/／]\s*\d+\s*$")


def _sanitize_pdf_toc(pdf_toc: list) -> list:
    """PDF 书签先验清洗：识别并拒收'标题／页码'平级假目录与页码书签洪水。

    扫描本的自制书签常把印刷页码粘在标题里（'第一章 函数与极限／1'）且
    level 全平——这不是结构真值：采用后页码会经锚点富化（b[content]=m[2]）
    粘进正文标题，全平层级把目录压成一条一章（病例019 高等数学实测
    193/198 条命中，目录 72 条碎成 191 条）。≥80% 条目带页码尾巴即整体
    丢弃，回退 LLM 目录提取（伪造指纹兜底仍生效）；零散尾巴逐条剥除。

    页码书签洪水（病例 025 续·汉语语义学）：另一类扫描本自制书签把每个
    正文页的**页码本身**录成一条书签（'1'、'2'…'101'，432/436 条纯数字）。
    短数字串不在旧的 ≥6 位长串拒收范围内，漏网后经位置锚定把'前言'锁成
    唯一 L1 锚点，179 个无编号标题（含全部章题）被下沉压成 前言>平铺 L2。
    纯数字条目任何长度一律拒收（目录里不存在纯数字真标题）；≥80% 纯数字
    即整体丢弃。失败方向=回退 LLM 目录/形状栈，与无书签书同路径，不更差。
    """
    if not pdf_toc:
        return []
    n = len(pdf_toc)
    n_junk = sum(1 for e in pdf_toc
                 if _JUNK_BOOKMARK_RE.search((e or {}).get("text") or ""))
    if n >= 5 and n_junk >= 0.8 * n:
        logger.warning(
            f"  PDF 书签疑似'标题／页码'假目录（{n_junk}/{n} 条带页码尾巴），"
            f"丢弃 outline 先验，回退 LLM 目录提取")
        return []
    n_digit = sum(1 for e in pdf_toc
                  if re.fullmatch(r"\d+", ((e or {}).get("text") or "").strip()))
    if n >= 5 and n_digit >= 0.8 * n:
        logger.warning(
            f"  PDF 书签疑似页码书签洪水（{n_digit}/{n} 条纯数字条目），"
            f"丢弃 outline 先验，回退 LLM 目录提取")
        return []
    out = []
    for e in pdf_toc:
        text = _JUNK_BOOKMARK_RE.sub("", (e.get("text") or "").strip()).strip()
        if not text:
            continue
        # 纯数字条目（ISBN 长串、页码书签）无结构信息，拒收——否则会经
        # 页码救援/位置锚定合成幻影标题或毒化锚点表（病例 022 / 病例 025 续）
        if re.fullmatch(r"\d+", text):
            continue
        out.append({**e, "text": text})
    return out


def _build_anchors(toc_entries: list) -> list:
    """目录条目 → 锚点表 [(归一化键, level, 显示文本, 印刷页码|None)]

    剥离尾部页码（"第一章 …… 23"、"xxx / 060"）之外，若剥离改变了文本，
    **同时保留完整形态**：标题本身以数字结尾时（"one 7 JANUARY 1976"），
    剥尾会把年份吃掉，导致正文标题永远锚不上。完整形态在前，精确命中优先取它。
    """
    def _weak_key(k: str) -> bool:
        # 退化键拒收：有效字符（词字符/CJK）不足 3 且无 CJK（'VI.'→'vi.'
        # 这类截断残渣）。短键是模糊匹配误收短垃圾块的总开关
        # （病例 Feeling Great：'total'/'1._____' 等 4-7 字符块全部命中
        # 'vi.' 被锁 L1）
        core = re.sub(r"[^\w一-鿿]", "", k)
        return len(core) < 3 and not any('一' <= c <= '鿿' for c in core)

    anchors = []
    for e in toc_entries or []:
        raw = (e.get("text") or "").strip()
        if not raw:
            continue
        display = _strip_trailing_page(raw)
        full_key = _normalize_title(raw)
        key = _normalize_title(display)
        if _weak_key(full_key):
            continue
        try:
            level = int(e.get("level", 0))
        except (TypeError, ValueError):
            continue
        page = e.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        if level <= 0:
            continue
        if full_key:
            anchors.append((full_key, level, raw, page))
        if key and key != full_key and not _weak_key(key):
            anchors.append((key, level, display, page))

    # 两级目录且高层级只有个别无编号条目（LLM 常把 Foreword/Introduction
    # 拔高一级，实际与正文各章平级）→ 收敛到多数层级。
    # 真"编/Part"形状（第X编/Part X）的不动——那是真实的两部结构。
    uniq = {(a[2], a[3]): a for a in anchors}     # 双形态键去重后判定
    levels = Counter(a[1] for a in uniq.values())
    if len(levels) == 2:
        hi, lo = sorted(levels)
        hi_entries = [a for a in uniq.values() if a[1] == hi]
        if len(hi_entries) <= 2 and levels[lo] >= 5 and all(
                _title_shape(a[2]) == "plain" for a in hi_entries):
            anchors = [(k, lo, d, p) for (k, _lv, d, p) in anchors]
            logger.info(f"  目录层级收敛: {len(hi_entries)} 个无编号 "
                        f"L{hi} 条目并入 L{lo}（与正文各章平级）")
    return anchors


# 尾部匹配的通用小节名词表（病例 034 幻影挂账，用户批准修复）：
# 'Exercises' 单个通用词尾配 '12 Solutions to exercises' → 幻影章。
# 这些词只许精确命中，不得做锚点尾部；'RUN'/'The Dance' 等专名不受影响。
_GENERIC_TAIL_WORDS = frozenset({
    "exercise", "exercises", "summary", "introduction", "conclusion",
    "conclusions", "overview", "notes", "problem", "problems", "solution",
    "solutions", "example", "examples", "remark", "remarks", "appendix",
    "index", "references", "bibliography",
})


def _is_generic_tail_word(key: str) -> bool:
    """块是否单个通用小节名（已归一化 casefold）——是则禁止锚点尾部匹配。"""
    return key.isalpha() and key in _GENERIC_TAIL_WORDS


# 纯日期形标题键（归一化后无空格小写）：'7january1976'
_DATE_SHAPE_RE = re.compile(r"^\d{1,2}[a-z]+\d{4}$")


def _match_anchor(text: str, anchors: list):
    """归一化匹配锚点：精确 > 块是锚点前缀 > 块是锚点尾部（分隔页模式）
    > 有界编辑距离（容忍 OCR 单字差异）。

    不做"中间包含"匹配——会把 '权利主体' 错配到
    '第一节 作为权利主体的自然人' 这类更长条目上。
    返回匹配的锚点元组 (key, level, display, page)，未匹配返回 None。
    """
    key = _normalize_title(text)
    if not key:
        return None
    # 编号前缀剥离键（'Problem 3.1. The fine…' → 'thefine…'）：印刷目录
    # 常省略 Problem/Exercise/Example 编号，条目与正文标题差一个前缀
    # （病例 QFT：习题章的 11 条节标题因此锚不上）。严格限定词表+必须
    # 带数字编号，防过匹配；只补精确命中，不进模糊兜底。
    alt_key = _PROBLEM_PREFIX_RE.sub("", key)
    prefix_best = None
    suffix_best = None
    long_prefix_best = None
    long_suffix_best = None
    for a in anchors:
        k, lv = a[0], a[1]
        if k == key or (alt_key != key and k == alt_key):
            return a
        # 前缀匹配需最低长度：单字母 'A' 会前缀命中 'Acknowledgments'
        # （病例 Feeling Great 索引字母字头锚定成 L1）；2 字以上 CJK 前缀仍允许
        if k.startswith(key) and len(k) > len(key) \
                and (len(key) >= 3 or any('一' <= c <= '鿿' for c in key)):
            # 块是锚点的前缀（"第一章" → "第一章 民法概念论"），取最长
            if prefix_best is None or len(k) > len(prefix_best[0]):
                prefix_best = (k, a)
        elif len(key) >= 3 and k.endswith(key) and len(k) > len(key) \
                and not _is_generic_tail_word(key):
            # 块是锚点的尾部（"权利主体" → "第二编 权利主体"、
            # "RUN" → "Chapter 1: Run"），取最短
            if suffix_best is None or len(k) < len(suffix_best[0]):
                suffix_best = (k, a)
        elif len(k) >= 6 and key.startswith(k) and len(key) > len(k):
            # 锚点是块的前缀（目录条目被截断，如 '9.2.1 Kau' →
            # '9.2.1 Kauzmann paradox'；限长锚点防 '1.1' 误配 '1.1.2'）
            if long_prefix_best is None or len(k) > len(long_prefix_best[0]):
                long_prefix_best = (k, a)
        elif len(k) >= 8 and key.endswith(k) and len(key) > len(k) \
                and key[: len(key) - len(k)].endswith((":", "：")):
            # 锚点是块的尾部，块多一个冒号标签前缀（'Complement: Isospin
            # and flavor SU(3)' ↔ 目录 'Isospin and flavor SU(3)'，病例 024）；
            # 冒号限定防过匹配（裸后缀会把 '绪论' 错配到 '附录：绪论'），取最长
            if long_suffix_best is None or len(k) > len(long_suffix_best[0]):
                long_suffix_best = (k, a)
    if prefix_best is not None:
        return prefix_best[1]
    if suffix_best is not None:
        return suffix_best[1]
    if long_prefix_best is not None:
        return long_prefix_best[1]
    if long_suffix_best is not None:
        return long_suffix_best[1]
    # 块是锚点的子串（副标题被 OCR 截断，如"…——当代新"缺尾字）；
    # 限长块防"权利主体"式短块错配，取最短包含锚点（最具体）。
    # 覆盖率闸门：块须覆盖锚点 ≥40%——单个英文词也能混过 8 字符下限
    # （病例 Feeling Great：quiz 表头 'Depression' 10 字符子串命中 41 字符的
    # 分区条目 'I. How to Turn Depression and Anxiety into Joy' → 幻影 L1）
    if len(key) >= 8 and not _is_generic_tail_word(key):
        sub_best = None
        for a in anchors:
            k = a[0]
            if len(key) < max(8, 0.4 * len(k)):
                continue
            if key in k and len(k) > len(key):
                if sub_best is None or len(k) < len(sub_best[0]):
                    sub_best = (k, a)
        if sub_best is not None:
            return sub_best[1]
    # 日期形强键（自迭代批·必须保卫社会：无编号讲稿章，正文标题是纯日期
    # '7 JANUARY 1976'，目录条目含同一日期串+摘要）——日期作强键锚定；
    # 多个同日期条目取最短（最具体），完全同长才算歧义放弃（不动作）
    if _DATE_SHAPE_RE.fullmatch(key):
        date_hits = [a for a in anchors
                     if re.search(r"(?<!\d)" + re.escape(key) + r"(?!\d)", a[0])]
        if date_hits:
            date_hits.sort(key=lambda a: len(a[0]))
            # 歧义判定：剥掉日期串后余部不同 = 不同条目共享日期（数据异常）
            # → 不动作；余部相同 = 简目/详目重复条目 → 取最短（最具体）
            remainders = {a[0].replace(key, "", 1) for a in date_hits}
            if len(remainders) == 1:
                return date_hits[0]
            return None  # 歧义明确不动作，不落入模糊兜底
    # 模糊兜底：目录页与正文的 OCR 结果常有单字差异（僵/催、是/和、缺字）
    if len(key) >= 4:
        best = None
        best_dist = None
        ambiguous = False
        for a in anchors:
            k = a[0]
            # 系列标题守卫：块 = 锚点 + 数字/字母后缀（'答学友问1' vs '答学友问'）
            # 是系列中的另一项而非 OCR 误差，不得模糊命中
            if key.startswith(k) and len(key) > len(k) \
                    and (key[len(k)].isdigit()
                         or (len(key) - len(k) == 1 and key[len(k)].isalpha()
                             and key[len(k)].isascii())):
                continue
            # 容错上限按两者较长者定（LLM 笔误可能让条目比正文长，
            # 如 'PRÉSPACE' vs 'PRÉFACE'，块长 7 但条目长 8 需容 2）
            limit = 1 if max(len(key), len(k)) < 8 else 2
            d = _edit_distance_le(key, k, limit)
            # 超限时 _edit_distance_le 返回 limit+1，必须拒绝——
            # 病例 Feeling Great：旧版 best_dist 起始 3，短锚点超限返回 2
            # 仍被接受，'total'/'1._____' 等短垃圾块全部命中退化锚 'vi.'
            if d > limit:
                continue
            if best_dist is None or d < best_dist:
                best_dist, best, ambiguous = d, a, False
            elif d == best_dist and a is not best:
                ambiguous = True
        if best is not None and not ambiguous:
            return best
    return None


def _calibrate_levels(blocks: list, toc_entries: list,
                      toc_pages: set | None = None) -> int:
    """校准结构模型的漂移层级：TOC 锚点 + 形状栈。

    背景：分块推理的 level 只在局部分块内自洽，跨块会漂移；
    且单个分块的判定本身可能失真。因此绝对 level 不可信，
    能用的只有两类稳定信号：

    1. **TOC 锚点**（绝对真值）：正文标题与目录条目匹配 → level 锁定。
    2. **形状栈**（通用先验 + 锚点标定）：编号形状（第X章/一、/（一）/1. …）
       的相对深度在全书是稳定的。排名键：锚定形状取 TOC 真实 level，
       未锚定形状按通用编号次序外推（锚定 level + 0.5 + 微偏移）。
       经典大纲栈推理：同形同级（兄弟替换）、新深形 +1（嵌套）、浅形回弹出栈。

    直接覆写 block["level"]，原值备份到 block["level_raw"]。
    返回锚点命中数。
    """
    # ── 目录锚点表 ──
    anchors = _build_anchors(toc_entries)

    def match_anchor(text: str):
        m = _match_anchor(text, anchors)
        return m[1] if m else None

    # ── 目录页标题降格（toc_pages 由 _repair_toc_pages 在目录页码重配时
    # 识别：≥3 条目命中且 ≥3 数字块的页，只可能是目录页；正文标题密集页
    # 没有那么多数字块，不会误伤）──
    # 目录页上的条目块与锚点天然匹配，进树会在目录页位置切出假章节，
    # 一律降格为普通文本（目录内容按正文渲染，不进树）。
    toc_pages = toc_pages or set()
    if toc_pages:
        demoted = 0
        for b in blocks:
            if b.get("page") in toc_pages and b.get("type") == "title" \
                    and b.get("level", -1) > 0:
                b["type"] = "text"
                b["level"] = -1
                demoted += 1
        if demoted:
            logger.info(f"  目录页降格: {demoted} 个目录条目块不进入文档树"
                        f"（页 {sorted(toc_pages)}）")

    # ── 锚点驱动的标题救援 ──
    # 编/章分隔页常被模型漏判（大字孤立、无上下文）；
    # 与目录条目匹配的短文本块，按目录定义强制晋升为标题。
    # 长度上限 64：含公式的节标题会超过 40（数学书 '一、f(x)=e^{λx}P_m(x)型'）
    #
    # 位置闸门（病例 Feeling Great：'CliffsNotes 精华版'章内含全书章节摘要
    # 表，表内章节名逐条命中锚点被晋升 → 目录乱序/重复章）：先用已锚定
    # 标题估计 印刷页→扫描页 偏移；纯文本块须落在预测位置 ±8 页内。
    # 闸门只挡"毫无标题视觉证据的纯文本块"——引擎已标标题的块不套用：
    # 前置页罗马页码（Acknowledgments ix）、附录另起页码（刘擎'补充讲解'
    # 印刷 289 → 扫描 371）等 regime 下全局偏移本就不成立。
    from statistics import median as _median
    offsets = [
        b["page"] - a[3]
        for b in blocks
        if b.get("type") == "title" and b.get("level", -1) > 0 and b.get("page")
        for a in [_match_anchor((b.get("content") or "").strip(), anchors)]
        if a is not None and a[3] is not None
    ]
    page_offset = round(_median(offsets)) if len(offsets) >= 3 else None

    rescued = 0
    for b in blocks:
        if b.get("type") == "title" and b.get("level", -1) > 0:
            continue
        if b.get("type") in ("header", "footer", "page_number",
                             "aside_text", "discarded"):
            continue          # 噪声类型永不晋升——运行头 suffix 命中锚点
                              # 顶替真章题的病例（QFT ch4）
        if b.get("page") in toc_pages:
            continue
        text = (b.get("content") or "").strip()
        key = _normalize_title(text)
        # 长度上限只挡非标题块：引擎已标 title 的块有视觉证据（闸门同理不套
        # 位置约束）——多行 ALL-CAPS 超长章题（BAC ch12，归一化 105 字符）
        # 曾被 64 上限漏过，锚点在手也拿不到 level
        if not key or (len(key) > 64 and b.get("type") != "title"):
            continue
        m = _match_anchor(text, anchors)
        if m is None:
            continue
        if b.get("type") != "title" and page_offset is not None \
                and m[3] is not None and b.get("page"):
            if abs(b["page"] - (m[3] + page_offset)) > 8:
                continue
        b["type"] = "title"
        b["level"] = m[1]
        rescued += 1
    if rescued:
        logger.info(f"  标题救援: {rescued} 个漏判标题由目录锚点晋升")

    # ── 收集标题并赋形状 ──
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]

    # ── 图注/表注位置过滤 ──
    # 图注在图下、表注在表上：与图/表块 x 重叠、垂直紧贴（≤3% 页高），
    # 且字号（块高）不明显大于正文中位行高。锚得上的绝不动——
    # 锚点是比几何更强的证据（'图 3-1' 样式的真节标题不会被误杀）。
    from statistics import median as _median
    page_med: dict = {}
    for b in blocks:
        bb = b.get("bbox")
        if b.get("type") == "text" and bb:
            h = bb[3] - bb[1]
            if h > 0.003:
                page_med.setdefault(b.get("page"), []).append(h)
    page_med = {p: _median(v) for p, v in page_med.items() if v}

    def _is_caption_geom(b) -> bool:
        bb = b.get("bbox")
        if not bb:
            return False
        med = page_med.get(b.get("page"))
        if med and (bb[3] - bb[1]) > 1.6 * med:
            return False      # 字号明显大于正文，不像图注
        for o in blocks:
            if o.get("page") != b.get("page") \
                    or o.get("type") not in ("image", "table"):
                continue
            ob = o.get("bbox")
            if not ob:
                continue
            xov = min(bb[2], ob[2]) - max(bb[0], ob[0])
            if xov <= 0 or xov < 0.3 * max(bb[2] - bb[0], 1e-6):
                continue
            if o["type"] == "image" and -0.005 <= bb[1] - ob[3] <= 0.03:
                return True   # 图注在图下
            if o["type"] == "table" and -0.005 <= ob[1] - bb[3] <= 0.03:
                return True   # 表注在表上
        return False

    n_cap = 0
    for b in titled:
        text = (b.get("content") or "").strip()
        if not text or len(text) > 200:
            continue
        if match_anchor(text) is not None:
            continue              # 锚得上 = 真标题，几何证据让位
        if _is_caption_geom(b):
            b["type"] = "text"
            b["level"] = -1
            n_cap += 1
    if n_cap:
        titled = [b for b in titled if b.get("level", -1) > 0]
        logger.info(f"  图注过滤: {n_cap} 个贴图/贴表小字块降回正文")
    if not titled:
        return 0

    for b in titled:
        b["level_raw"] = b["level"]

    # ── 形状排名键 ──
    # 锚定形状的排名键 = TOC 锚点层级的真实值（绝对真值）；
    # 未锚定形状 = 按通用编号次序取其后一个锚定形状的 level + 微小偏移。
    def median(vs):
        vs = sorted(vs)
        n = len(vs)
        return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2

    anchor_shape_votes = {}
    for _k, lv, display, _p in anchors:
        anchor_shape_votes.setdefault(_title_shape(display), []).append(lv)
    anchor_shape_key = {s: median(vs) for s, vs in anchor_shape_votes.items()}

    pattern_names = [name for name, _ in _SHAPE_PATTERNS]

    def shape_key(shape: str) -> float:
        if shape in anchor_shape_key:
            return anchor_shape_key[shape]
        if shape == "plain":
            return -1.0  # 无编号标题：按顶层处理，level 走首票/锚点
        if anchor_shape_key:
            # 编号次序中其后最近的锚定形状：其 level + 0.5 + 级内微偏移
            best = None
            for name in pattern_names:
                if name == shape:
                    break
                if name in anchor_shape_key:
                    best = anchor_shape_key[name]
            if best is not None:
                offset = pattern_names.index(shape) - max(
                    (i for i, n in enumerate(pattern_names)
                     if n in anchor_shape_key and pattern_names.index(n) < pattern_names.index(shape)),
                    default=0)
                return best + 0.5 + 0.01 * offset
        # 无锚点：纯通用编号次序
        return float(pattern_names.index(shape)) if shape in pattern_names else 50.0

    anchored_keys = {s: round(v, 2) for s, v in anchor_shape_key.items()}
    logger.info(f"  锚定形状排名键: {anchored_keys}")

    # ── 形状栈推理 ──
    stack = []            # [(key, level, shape)]
    last_shape_level = {} # shape → 最近一次 level（出栈时兜底）
    hits = 0

    for b in titled:
        text = (b.get("content") or "").strip()
        shape = _title_shape(text)
        key = shape_key(shape)
        m = _match_anchor(text, anchors)
        toc_level = m[1] if m else None

        if toc_level is not None:
            # 富化：块只是锚点条目的前缀/尾部/子串（章名竖排被 OCR
            # 拆块或截断）→ 用目录完整文字替换，保证渲染标题完整
            bkey = _normalize_title(text)
            if m[0] != bkey and bkey in m[0]:
                b["content"] = m[2]
            # 锚点锁定，并把栈重置到该层级。
            # 压栈键用真实层级而非形状排名键：章名块与节标题可能同形状
            # （如均无编号/plain），用形状键会同键碰撞——后续兄弟节标题
            # 弹栈时把父章一并弹出，空栈兜底再取到被污染的层级。
            while stack and stack[-1][1] >= toc_level:
                last_shape_level[stack[-1][2]] = stack[-1][1]
                stack.pop()
            b["level"] = toc_level
            b["_anchored"] = True
            stack.append((float(toc_level), toc_level, shape))
            hits += 1
            logger.info(f"    锚点 P{b.get('page')} L{b['level_raw']}→L{toc_level} "
                        f"{text[:30]}")
        else:
            # 弹出同级及更深的形状（同级标题 = 兄弟，应替换而非嵌套）
            while stack and stack[-1][0] >= key:
                last_shape_level[stack[-1][2]] = stack[-1][1]
                stack.pop()
            if stack:
                b["level"] = stack[-1][1] + 1
            else:
                # 栈空：优先同形状历史值，否则首票
                b["level"] = last_shape_level.get(shape, b["level_raw"])
            stack.append((key, b["level"], shape))
        last_shape_level[shape] = b["level"]

    # ── 运行头收敛：同一标题文本在更早页面已出现过（且本块位于页首
    # y2≤10%），是页眉重复而非新标题 → 降回正文（锚点已被首次出现消费，
    # 重复块即使锚得上也是页眉）。同页重复由后面的同文去重处理。
    seen_title_page: dict = {}
    n_rh = 0
    for b in titled:
        k = _normalize_title(b.get("content") or "")
        bb = b.get("bbox")
        if (k and bb and k in seen_title_page
                and b.get("page", 0) > seen_title_page[k]
                and bb[3] <= 0.10):
            b["type"] = "text"
            b["level"] = -1
            n_rh += 1
        else:
            seen_title_page.setdefault(k, b.get("page", 0))
    if n_rh:
        titled = [b for b in titled if b.get("level", -1) > 0]
        logger.info(f"  运行头收敛: {n_rh} 个页首重复标题降回正文")

    # ── 孤儿编号系列（先救后罚，详见 _fix_orphan_series） ──
    _fix_orphan_series(blocks)
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]

    # ── 同页同文标题去重（引擎重复块/页眉混入，或两个碎块被锚点富化成
    # 同一完整标题，如 'nine' + '3 MARCH 1976' → 两个 'nine 3 MARCH 1976'；
    # 保留首个）──
    seen_tt: set = set()
    dup_ids = set()
    for b in titled:
        k = (_normalize_title(b.get("content") or ""), b.get("page"))
        if k in seen_tt:
            dup_ids.add(id(b))
        else:
            seen_tt.add(k)
    if dup_ids:
        blocks[:] = [b for b in blocks if id(b) not in dup_ids]
        titled = [b for b in titled if id(b) not in dup_ids]
        logger.info(f"  重复标题去重: {len(dup_ids)} 个同页同文标题块移除")

    # ── 同页缩写重复：去标点/前导编号后一个是另一个的前缀（运行头
    # '21 | 阿伦特Ⅱ' vs 章名 '21 阿伦特Ⅱ：怎么才能不变成坏人'、
    # 'Crystal structure' vs '1 Crystal structure'）→ 同级时弃缩写形 ──
    def _core(text: str) -> str:
        t = _normalize_title(text)
        t = re.sub(r"^\d+[.、|]?\s*", "", t)
        t = re.sub(r"^第[一二三四五六七八九十百零〇0-9]+[章节编篇卷部]", "", t)
        return re.sub(r"[^\w]", "", t)

    abbrev_ids = set()
    by_page: dict = {}
    for b in titled:
        by_page.setdefault(b.get("page"), []).append(b)
    for _pg, bs in by_page.items():
        if len(bs) < 2:
            continue
        cores = [(b, _core(b.get("content") or "")) for b in bs]
        for i, (b, ck) in enumerate(cores):
            if not ck or id(b) in abbrev_ids:
                continue
            for j, (b2, ck2) in enumerate(cores):
                if i == j or id(b2) in abbrev_ids or not ck2.startswith(ck):
                    continue
                if b.get("level") != b2.get("level"):
                    continue
                if len(ck) < len(ck2):
                    abbrev_ids.add(id(b))
                    break
                if ck == ck2:
                    c1, c2 = b.get("content") or "", b2.get("content") or ""
                    if len(c1) < len(c2) or (len(c1) == len(c2) and i > j):
                        abbrev_ids.add(id(b))
                        break
    if abbrev_ids:
        blocks[:] = [b for b in blocks if id(b) not in abbrev_ids]
        titled = [b for b in titled if id(b) not in abbrev_ids]
        logger.info(f"  缩写标题去重: {len(abbrev_ids)} 个同页缩写标题块移除")

    logger.info(f"  TOC 锚定校正: {hits} 个锚点命中（共 {len(titled)} 个标题）")
    return hits


# 目录页码块：纯阿拉伯数字或罗马数字（PaddleOCR 把目录页码排为独立
# aside_text 块时的形态）
_TOC_NUM_RE = re.compile(r"^(\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$")
# 点线引导行：'第二节 法人的分类 ..... 153'、'xxx …… 60'——几乎只出现在
# 目录/索引页，是最强的目录页信号（与引擎无关，比条目文本匹配更本质）
_LEADER_LINE_RE = re.compile(r"[.…·_—–]{2,}\s*(\d{1,4})\s*$")


def _roman_to_int(s: str) -> int | None:
    """罗马数字 → int（ix→9, XV→15）；非法返回 None。"""
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = vals.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total if total > 0 else None


def _repair_toc_pages(toc_entries: list, content_list: list) -> list:
    """目录页码修复：页码与条目分离的目录版式（PaddleOCR 把页码排成独立
    aside_text 数字块）下，LLM 提取 toc_entries 时无法配对页码，会编出
    等差数列——进而让页码救援在错误位置造幻影标题块。

    这里用确定性规则重配：目录页上条目块与数字块按 bbox 的 y 坐标同行
    对齐（目录排版里页码恒与条目同行），配上的条目用真实印刷页码覆写；
    配不上（如 MinerU 页码本就内嵌在条目文本里）保持原样。

    返回 (toc_entries, toc_pages)：toc_pages 为识别出的目录页 page_idx
    集合（≥3 条目命中且 ≥3 数字块的页，只可能是目录页），供
    _calibrate_levels 把目录条目块降格、防止混进文档树。
    """
    if not toc_entries or not content_list:
        return toc_entries, set()

    # 每页收集：数字块 [(y, text)]、文本块 [(y, 归一化文本)]
    pages: dict[int, dict] = {}
    for b in content_list:
        pi = b.get("page_idx")
        bbox = b.get("bbox")
        text = (b.get("text") or "").strip()
        if pi is None or not bbox or not text:
            continue
        y = (float(bbox[1]) + float(bbox[3])) / 2
        slot = pages.setdefault(int(pi), {"nums": [], "texts": []})
        if b.get("type") in ("aside_text", "page_number") and _TOC_NUM_RE.match(text):
            slot["nums"].append((y, text))
        elif b.get("type") in ("text", "title"):
            slot["texts"].append((y, _normalize_title(_strip_trailing_page(text))))

    if not any(p["nums"] for p in pages.values()):
        return toc_entries, set()

    # 每个条目找它的目录页命中块：[(entry_idx, page, y)]
    hits: dict[int, list] = {}   # page → [(entry_idx, y)]
    for i, e in enumerate(toc_entries):
        raw = (e.get("text") or "").strip()
        key = _normalize_title(_strip_trailing_page(raw))
        if not key:
            continue
        for p, slot in pages.items():
            if not slot["nums"]:
                continue
            for y, tkey in slot["texts"]:
                if tkey == key:
                    hits.setdefault(p, []).append((i, y))
                    break

    # 目录页 = ≥3 条目命中且 ≥3 数字块的页（正文标题密集页数字块通常
    # 只有页脚一个，不会误判）
    toc_pages = {p for p, pairs in hits.items()
                 if len(pairs) >= 3 and len(pages[p]["nums"]) >= 3}

    # 逐页同行配对：按 |Δy| 全局贪心，每个数字只配一次
    repaired = 0
    for p in toc_pages:
        pairs = hits[p]
        nums = sorted(pages[p]["nums"])
        # 同行配对：按 |Δy| 全局贪心，每个数字只配一次
        cands = []
        for i, y in pairs:
            for ny, ntext in nums:
                dy = abs(ny - y)
                if dy <= 25:      # 千分位坐标，≈1 行高容差
                    cands.append((dy, i, ntext))
        cands.sort()
        used_nums, used_entries = set(), set()
        for dy, i, ntext in cands:
            if i in used_entries or ntext in used_nums:
                continue
            used_entries.add(i)
            used_nums.add(ntext)
            if ntext.isdigit():
                page_val = int(ntext)
            else:
                # 罗马数字页码属于前置部分，与正文偏移 regime 不同，
                # 喂给页码救援只会按正文偏移算出错误位置 → 页码置空
                # （锚点匹配即视为满足，不参与页码救援）
                if _roman_to_int(ntext) is None:
                    continue
                page_val = None
            old = toc_entries[i].get("page")
            if old != page_val:
                toc_entries[i] = {**toc_entries[i], "page": page_val}
                repaired += 1
    if repaired:
        logger.info(f"  目录页码修复: {repaired} 条按页内数字块同行重配")
    return toc_entries, toc_pages


def _detect_toc_pages_by_entries(toc_entries: list, content_list: list) -> set:
    """目录页识别（按条目行命中）：页内"精确命中目录条目的行"≥3，且
    （纯数字行 ≥3 或 命中行占全页非空行 ≥50%），且命中条目的印刷页码
    跨度 >5 页。

    覆盖三种目录形态：独立条目块（页码分离或简目）、点线页码合并成一段的
    blob 块（按行拆开匹配）、页码内嵌。两道防误判：
    - 正文标题密集页（一章两节同页）：命中少、占比低；
    - 章扉页/章首页（章标题 + 本章节目标题，排版上像小目录）：命中条目
      印刷页码集中在同一章起始页（跨度≈0）；目录页条目指向全书——
      哪怕只覆盖一章的小节（详目单页），跨度也有数页到数十页。
    另做邻页扩展：主检出页的相邻页命中 ≥2 也视为目录页（长目录的残余页，
    如只列两三编的末页）。
    另有独立的点线引导行判据（≥3 行以点线+页码结尾且页码跨度 >5），
    不依赖目录条目文本，条目缺失/未匹配时也能识别目录页。
    返回 page_idx（0 起）集合。
    """
    page_lines: dict[int, list] = {}
    for b in content_list:
        if b.get("type") not in ("text", "title", "aside_text",
                                 "page_number", "header", "footer"):
            continue
        pi = b.get("page_idx")
        if pi is None:
            continue
        for line in (b.get("text") or "").split("\n"):
            line = line.strip()
            if line:
                page_lines.setdefault(int(pi), []).append(line)

    # 点线引导行判据（独立通道，不依赖条目文本匹配）：
    # ≥3 行以点线+页码结尾，且这些页码跨度 >5（章内小目录跨度小，安全）
    leader_pages = set()
    for p, lines in page_lines.items():
        nums = [int(m.group(1)) for line in lines
                if (m := _LEADER_LINE_RE.search(line))]
        if len(nums) >= 3 and max(nums) - min(nums) > 5:
            leader_pages.add(p)

    entries = []
    for i, e in enumerate(toc_entries or []):
        raw = (e.get("text") or "").strip()
        k = _normalize_title(_strip_trailing_page(raw))
        if k:
            entries.append((k, e.get("page"), i))
    if not entries:
        return leader_pages
    key_map = {}
    for k, pg, i in entries:
        key_map.setdefault(k, []).append((pg, i))

    def page_stat(lines) -> tuple[int, int, int]:
        """(命中数, 数字行数, 命中条目印刷页跨度)；无页码信息时跨度记为 10**9。"""
        hits = nums = 0
        printed = []
        for line in lines:
            k = _normalize_title(_strip_trailing_page(line))
            if k and k in key_map:
                hits += 1
                for pg, _i in key_map[k][:1]:
                    if isinstance(pg, int):
                        printed.append(pg)
            elif _TOC_NUM_RE.match(line):
                nums += 1
        span = (max(printed) - min(printed)) if len(printed) >= 2 \
            else (10**9 if not printed else 0)
        return hits, nums, span

    toc_pages = set(leader_pages)
    for p, lines in page_lines.items():
        hits, nums, span = page_stat(lines)
        if hits >= 3 and span > 5 and (nums >= 3 or hits >= 0.5 * len(lines)):
            toc_pages.add(p)

    # 邻页扩展：长目录末页（残余两三条目）挂靠主检出页
    for p, lines in page_lines.items():
        if p in toc_pages:
            continue
        if (p - 1 in toc_pages or p + 1 in toc_pages):
            hits, nums, span = page_stat(lines)
            if hits >= 2 and span > 5:
                toc_pages.add(p)
    return toc_pages


def _forged_toc_fingerprint(toc_entries: list, blocks: list) -> tuple[int, int]:
    """伪造目录指纹：返回 (可比对条目数, 页码与标题块扫描页完全相等的条目数)。

    真目录条目给印刷页码，与标题块的扫描页通常有非零偏移；书里没有目录页
    时 LLM 会拿"全书标题列表"编造目录，page 直接抄列表里的扫描页码，
    全部完全相等。同一锚点的多次命中（运行头）取最早页——条目抄的总是
    标题首次出现的那一页。
    """
    anchors = _build_anchors(toc_entries)
    if not anchors:
        return 0, 0
    first_hit: dict = {}   # 锚点键 → (条目页码, 标题块扫描页)
    for b in blocks:
        if b.get("type") != "title" or b.get("level", -1) <= 0:
            continue
        m = _match_anchor((b.get("content") or "").strip(), anchors)
        if not m or m[3] is None:
            continue
        pg = b.get("page", 0)
        if m[0] not in first_hit or pg < first_hit[m[0]][1]:
            first_hit[m[0]] = (m[3], pg)
    n_cmp = len(first_hit)
    n_eq = sum(1 for entry_pg, scan_pg in first_hit.values()
               if entry_pg == scan_pg)
    return n_cmp, n_eq


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_SERIES_FAMILIES = {
    "num_dot": (re.compile(r"^(\d+)[.、]\s*\S"), int),
    "num_cn": (re.compile(r"^([一二三四五六七八九十]+)、\s*\S"), _CN_NUM.get),
    "num_cn_paren": (re.compile(r"^[（(]([一二三四五六七八九十]+)[）)]\s*\S"),
                     _CN_NUM.get),
    "roman": (re.compile(r"^([IVXLCDM]+)[.、]\s"), _roman_to_int),
    "alpha": (re.compile(r"^([a-zA-Z])[.、]\s"), lambda s: ord(s.lower()) - 96),
}


def _series_of(text: str):
    """编号系列 (family, number)；无编号返回 None。"""
    t = (text or "").strip()
    for fam, (pat, conv) in _SERIES_FAMILIES.items():
        m = pat.match(t)
        if m:
            n = conv(m.group(1))
            if n:
                return fam, n
    return None


def _fix_orphan_series(blocks: list) -> None:
    """孤儿编号系列处理（先救后罚）。

    某编号家族（1./一、/I./a.）全书没有 '1' 而系列从 ≥2 起跳，几乎可以
    断定是列表项被误判成标题（'4.' 孤零零挂在层级顶，一定有 1./2./3.）：
    先在首个成员附近（±3 页）的文本块里找 '1' 晋升救回（先救）；
    找不到且系列 ≥3 起跳，把整个系列降回正文（后罚）。
    有 '1' 的家族健康，不动。作用域按家族全局近似（保守，只处理
    全书无 '1' 的家族，误伤面最小）。
    """
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]
    fams: dict[str, list] = {}
    for b in titled:
        s = _series_of(b.get("content"))
        if s:
            fams.setdefault(s[0], []).append((s[1], b))

    n_rescued = n_demoted = 0
    for fam, members in fams.items():
        nums = sorted(n for n, _ in members)
        if nums[0] < 2:
            continue                      # 家族健康（有 '1'）
        first = min(members, key=lambda nb: nb[1].get("page", 0))[1]
        pat, conv = _SERIES_FAMILIES[fam]
        rescued_one = False
        for b in blocks:
            if b.get("type") != "text":
                continue
            text = (b.get("content") or "").strip()
            m = pat.match(text)
            if not m or conv(m.group(1)) != 1:
                continue
            if not (2 <= len(text) <= 40) or text.endswith(("。", ".", ";", "；")):
                continue
            if abs(b.get("page", 0) - first.get("page", 0)) > 3:
                continue
            b["type"] = "title"
            b["level"] = first.get("level", 1)
            n_rescued += 1
            rescued_one = True
            logger.info(f"    系列救回: P{b.get('page')} {text[:30]}")
            break
        if rescued_one or nums[0] < 3:
            continue                      # 救回了，或 2 起跳（保守不罚）
        for n, b in members:
            b["type"] = "text"
            b["level"] = -1
            n_demoted += 1
    if n_rescued or n_demoted:
        logger.info(f"  孤儿编号: 救回 {n_rescued} 个, 降回正文 {n_demoted} 个")


def _split_by_cv(items: list, max_cv: float, max_groups: int) -> list:
    """[(height, payload)] → 按高度聚类的组列表（组均高降序）。

    算法参考 pdf-craft 的 common/cv_splitter.py（AGPL，此处按思想重写）：
    循环找 CV（变异系数 std/mean）最大的组，若其 CV > max_cv 且组数
    未达上限，则在组内按高度排序后的最大相邻间隔处二分；
    组内 ≤2 个元素不再拆。最大字号组 = rank 0。
    """
    if not items:
        return []
    groups = [sorted(items, key=lambda x: x[0])]
    while len(groups) < max_groups:
        best_i, best_cv = -1, 0.0
        for i, g in enumerate(groups):
            hs = [h for h, _ in g]
            mean = sum(hs) / len(hs)
            cv = 0.0 if mean <= 0 else \
                (sum((h - mean) ** 2 for h in hs) / len(hs)) ** 0.5 / mean
            if cv > best_cv:
                best_i, best_cv = i, cv
        g = groups[best_i]
        if best_cv <= max_cv or len(g) <= 2:
            break
        s = max(range(1, len(g)), key=lambda i: g[i][0] - g[i - 1][0])
        groups[best_i:best_i + 1] = [g[:s], g[s:]]
    groups.sort(key=lambda g: -sum(h for h, _ in g) / len(g))
    return groups


def _height_ladder_map(titled: list) -> dict:
    """标题块高度阶梯 → {id(block): 建议层级}，用锚定标题的真实层级
    标定 rank→level（几何证据，pdf-craft 式字号聚类）。

    每块的"字号"= bbox 高度（0..1，标题通常单行一块，无需多行聚合）。
    标定：每 rank 取组内锚定标题 level 的中位数；无锚定的 rank 向
    更浅（字号更大）的 rank 借一级，再借不到向更深的借。
    """
    from statistics import median as _med
    items = []
    for b in titled:
        bb = b.get("bbox")
        if bb and bb[3] - bb[1] > 0.003:
            items.append((bb[3] - bb[1], b))
    if len(items) < 8:
        return {}
    # max_cv 取 0.1：聚类宁细勿粗——锚定块层级锁死不受影响，
    # 无锚块的误分只会"过深"（保守方向），不会"过浅"
    groups = _split_by_cv(items, max_cv=0.1, max_groups=4)
    rank_lv = []
    for g in groups:
        lvs = [b["level"] for _, b in g
               if b.get("_anchored") and b.get("level", 0) > 0]
        rank_lv.append(_med(lvs) if lvs else None)
    for i in range(len(rank_lv)):
        if rank_lv[i] is None:
            for j in range(i - 1, -1, -1):
                if rank_lv[j] is not None:
                    rank_lv[i] = rank_lv[j] + 1
                    break
            if rank_lv[i] is None:
                for j in range(i + 1, len(rank_lv)):
                    if rank_lv[j] is not None:
                        rank_lv[i] = max(rank_lv[j] - (j - i), 1)
                        break
    out = {}
    for i, g in enumerate(groups):
        if rank_lv[i] is not None:
            for _, b in g:
                out[id(b)] = max(1, round(rank_lv[i]))
    return out


def _dedup_anchored_titles(blocks: list, toc_entries: list) -> int:
    """锚点身份查重：命中同一目录条目的多个标题块只留一个，其余降回正文
    （type=text, level=-1）。

    跨页重复的典型：章题页与章首页重复印刷标题、运行头 OCR 变体
    （'4 Karen's Story' / '4. Karen's Story'）。显示层文本查重会被译文
    措辞差异击败（'梅兰妮'/'梅琳达'），锚点身份与译文无关。

    留谁——目录序三明治一致性（不依赖印刷页码，免疫附录/罗马页码
    regime）：以"唯一命中的锚点块"为可信骨架，候选块的阅读位置若
    落在骨架相邻锚点的目录序区间外即幻影（病例 Feeling Great：引用框
    全文引用 '26. Let's Be Specific…' 夹在 13/14 章锚点之间；'Depression'
    单词块锚到分区 I 却位于第 3 章之后）。一致者优先，平级留阅读顺序
    首个（章题页在章首重复页之前）。
    失败方向安全：内容降格为段落保留，stage3 另有相邻近似章合并兜底。
    """
    anchors = _build_anchors(toc_entries)
    if not anchors:
        return 0
    order = {}
    for i, a in enumerate(anchors):
        order.setdefault(a[0], i)

    groups: dict = {}
    for b in blocks:
        if b.get("type") != "title" or b.get("level", -1) <= 0:
            continue
        text = (b.get("content") or "").strip()
        if not text:
            continue
        m = _match_anchor(text, anchors)
        if m:
            groups.setdefault(m[0], []).append(b)

    # 可信骨架：唯一命中的锚点块（无竞争的锚定），按扫描页排序
    skeleton = sorted(
        (bs[0].get("page") or 0, order[key])
        for key, bs in groups.items() if len(bs) == 1 and bs[0].get("page")
    )

    n = 0
    for key, bs in groups.items():
        if len(bs) < 2:
            continue
        my = order[key]
        keep = None
        for b in bs:
            pg = b.get("page") or 0
            prev_idx = max((oi for sp, oi in skeleton if sp < pg),
                           default=None)
            next_idx = min((oi for sp, oi in skeleton if sp > pg),
                           default=None)
            if (prev_idx is None or prev_idx <= my) and \
                    (next_idx is None or my <= next_idx):
                keep = b          # 阅读顺序上首个位置一致者
                break
        if keep is None:
            keep = bs[0]          # 骨架太稀无从判定 → 留首个
        for b in bs:
            if b is not keep:
                b["type"] = "text"
                b["level"] = -1
                b.pop("_anchored", None)
                n += 1
    if n:
        logger.info(f"  锚点查重: {n} 个同条目重复标题块降回正文"
                    f"（目录序一致性择优）")
    return n


# 无锚纯文字标题的垃圾形态（索引字母字头/孤字残片/署名行/单个英文词）。
# 这些形态若真是章/节标题，目录里一定有它（能锚上）；
# 锚不上 = 量表表头/页眉残片/落款，绝不可能是标题 —— 直接降回正文。
_JUNK_SINGLE_LETTER_RE = re.compile(r"^[A-Za-z]$")
_JUNK_SINGLE_CJK_RE = re.compile(r"^[一-鿿]$")
_JUNK_BYLINE_RE = re.compile(r"(?:^by\s+\S|[（(].*(?:著|译|主编)[)）]$|(?:著|编著|译注|主编|绘)$)",
                             re.I)
_JUNK_SINGLE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'’-]*$")
# 运行页眉形状（章名+节名+页码后缀：'第三章 习题 3-1（第 132 页）'）：
# 真标题绝不带（第 N 页）后缀——高数 p412（附录答案区）实测幻影标题。
_JUNK_RUNNING_HEAD_RE = re.compile(
    r"^第[一二三四五六七八九十百零〇0-9]+章\s*\S.*（第\s*\d+\s*页）\s*$")


def _veto_junk_titles(blocks: list, toc_entries: list) -> int:
    """无锚垃圾标题否决器：未锚定、无编号形状、且命中垃圾形态的标题块
    降回正文。只降格不晋升（失败方向=真实小节标题变正文段落，内容不丢）。
    病例 Feeling Great：'A'/'I'/'V'（索引字母字头）与 '马克·诺布尔博士 著'
    （署名行）成 L1/L2 标题上目录。

    单词 veto 的复发豁免：'Summary'/'Exercises' 这类章末固定小节在教科书里
    每章复发（病例 Condensed Matter 实测 14+ 次），复发 ≥3 的相同短标题几乎
    必是真小节，豁免；一次性浮头（FG 的 'Depression' 量表表头）才降格。
    """
    anchors = _build_anchors(toc_entries)
    # 无锚 plain 标题块的归一化文本复发计数（豁免判据）
    freq = Counter(
        _normalize_title((b.get("content") or "").strip())
        for b in blocks
        if b.get("type") == "title" and b.get("level", -1) > 0
        and not b.get("_anchored")
        and _title_shape((b.get("content") or "").strip()) == "plain"
    )
    n = 0
    for b in blocks:
        if b.get("type") != "title" or b.get("level", -1) <= 0:
            continue
        if b.get("_anchored"):
            continue              # 锚得上 = 目录认可的强证据，豁免
        text = (b.get("content") or "").strip()
        if _JUNK_RUNNING_HEAD_RE.match(text):
            # 运行页眉（章名+节名+页码后缀），任何形状都降回正文
            b["type"] = "text"
            b["level"] = -1
            n += 1
            continue
        if _title_shape(text) != "plain":
            continue              # 带编号形状的有形状栈管，不归这里
        if anchors and _match_anchor(text, anchors):
            continue              # 锚得上但未被锚定（如被查重降格前）= 不动
        if _JUNK_SINGLE_LETTER_RE.match(text) or _JUNK_SINGLE_CJK_RE.match(text) \
                or _JUNK_BYLINE_RE.search(text):
            b["type"] = "text"
            b["level"] = -1
            n += 1
        elif _JUNK_SINGLE_TOKEN_RE.match(text) \
                and freq[_normalize_title(text)] < 3:
            b["type"] = "text"
            b["level"] = -1
            n += 1
    if n:
        logger.info(f"  垃圾标题否决: {n} 个无锚短标题块降回正文")
    return n


def _sink_unanchored_plain(blocks: list) -> int:
    """无编号无锚标题下沉约束（在页码救援之后调用）。

    无编号标题若真是编/章级，目录里一定有它（锚得上，含 page_fuzzy
    位置验证晋升）；锚不上就说明它是篇内小标题，层级必须严格深于
    所属锚定章——不允许目录以外的小层级爬上来
    （刘擎'思想内在于现实'吸附到讲次同级的病例）。
    带编号形状的标题有形状栈管相对深度，不受此约束。
    """
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]
    ladder = _height_ladder_map(titled)
    last_anchor_level = None
    n_sink = n_geo = 0
    for b in titled:
        if b.get("_anchored"):
            last_anchor_level = b["level"]
            continue
        if _title_shape((b.get("content") or "").strip()) != "plain":
            continue
        if last_anchor_level is not None and b["level"] <= last_anchor_level:
            b["level"] = last_anchor_level + 1
            n_sink += 1
        # 字号阶梯差异化：同为无锚小标题，字小的应比字大的更深
        # （几何只许加深、不许上浮——下沉约束是硬边界）
        if last_anchor_level is not None:
            geo = ladder.get(id(b))
            if geo is not None and geo > b["level"]:
                b["level"] = geo
                n_geo += 1
    if n_sink:
        logger.info(f"  无锚下沉: {n_sink} 个无编号标题压到所属锚定章下一级")
    if n_geo:
        logger.info(f"  字号阶梯: {n_geo} 个小字标题按字高档位再下沉")
    return n_sink


# 泛名书签条目：'Chapter 1'/'Part II'/'Unit 3'/'第3章' 这类只有类别+序号、
# 无真实标题文字的 outline 条目（病例 022：文本锚定对它们全灭）
_GENERIC_OUTLINE_RE = re.compile(
    r"^(?:part|chapter|unit|module|section|volume|book|cap[íi]tulo|kapitel"
    r"|chapitre|第\s*[\dIVXLC一二三四五六七八九十]+\s*[章编篇卷部]?)"
    r"\s*[\dIVXLC]+\s*\.?$",
    re.I,
)


def _normalize_generic_outline_levels(toc_entries: list) -> list:
    """全平 outline（Part/Chapter 同层）且含泛名条目时，按标签类别重建
    层级：Part 类 → L1，Chapter 类 → L2（病例 022：CM 的 31 条书签 level
    全为 1，_spine_from_toc 读到平层 → spine 退化为 1，章/节全被压平）。
    Part 包含 Chapter 是普世约定；非全平或无 Part 类条目时原样返回。
    """
    def _lvl(e) -> int:
        try:
            return int(e.get("level", 0))
        except (TypeError, ValueError):
            return 0

    generic = [e for e in (toc_entries or [])
               if _GENERIC_OUTLINE_RE.match((e.get("text") or "").strip())]
    if len(generic) < 2:
        return toc_entries
    has_part = any(_title_shape((e.get("text") or "").strip())
                   in ("part_cn", "part_en") for e in generic)
    flat = len({_lvl(e) for e in toc_entries if _lvl(e) > 0}) <= 1
    if not (flat and has_part):
        return toc_entries
    out = []
    for e in toc_entries:
        text = (e.get("text") or "").strip()
        if _GENERIC_OUTLINE_RE.match(text):
            part_like = _title_shape(text) in ("part_cn", "part_en")
            out.append({**e, "level": 1 if part_like else 2})
        else:
            out.append(e)
    logger.info("  泛名书签层级重建: Part→L1, Chapter→L2（outline 全平）")
    return out


def _anchor_generic_outline(blocks: list, toc_entries: list) -> int:
    """泛名 PDF 书签条目的位置锚定（病例 022）。

    born-digital PDF 的书签目标页是扫描页真值（与印刷页无偏移问题），
    当条目是 'Chapter N' 泛名、文本锚定全灭时，把该页阅读顺序上首个
    未锚定的标题块锁到条目层级——书签页码即章首页，页顶块即章标题。

    层级推断：outline 全平（level 全同）时按标签类别重建——Part 类
    （part/编）→ L1，Chapter 类 → L2（存在 Part 类条目时）；否则沿用
    outline 原层级。非泛名条目、已锚定块、目录页降格区一律不动。
    """
    def _lvl(e) -> int:
        try:
            return int(e.get("level", 0))
        except (TypeError, ValueError):
            return 0

    generic = [e for e in (toc_entries or [])
               if _GENERIC_OUTLINE_RE.match((e.get("text") or "").strip())]
    if len(generic) < 2:
        return 0
    has_part = any(_title_shape((e.get("text") or "").strip())
                   in ("part_cn", "part_en") for e in generic)
    flat = len({_lvl(e) for e in toc_entries if _lvl(e) > 0}) <= 1

    n = 0
    for e in generic:
        text = (e.get("text") or "").strip()
        part_like = _title_shape(text) in ("part_cn", "part_en")
        if flat:
            level = 1 if part_like else (2 if has_part else (_lvl(e) or 1))
        else:
            level = _lvl(e)
        try:
            page = int(e.get("page"))
        except (TypeError, ValueError):
            continue
        # 章号（'Chapter 12' → '12.'），用于拒绝本章小节块（'12.1 …' 是节）
        mnum = re.search(r"\d+", text)
        chap_num = (mnum.group(0) + ".") if mnum else None

        # 候选块：书签页上的 title/header/text 块——章题常被 PaddleOCR 标成
        # header（页顶大字与页眉同位，病例 CM ch2 'Amorphous structure'），
        # 或投票缺失 level=0（病例 CM ch12 'Electrons…'）——未锚定、类标题
        # 长度、非垃圾形态、非本章小节
        cands = []
        for b in blocks:
            if b.get("page") != page or b.get("_anchored"):
                continue
            if b.get("type") not in ("title", "header", "text"):
                continue
            t = (b.get("content") or "").strip()
            k = _normalize_title(t)
            if not k or not 3 <= len(k) <= 64:
                continue
            if _JUNK_SINGLE_LETTER_RE.match(t) or _JUNK_SINGLE_CJK_RE.match(t) \
                    or _JUNK_BYLINE_RE.search(t):
                continue
            if chap_num and t.replace(" ", "").startswith(chap_num):
                continue
            cands.append(b)
        if not cands:
            continue
        # 页顶优先：章题在章首页顶部（章扉页不挂前章运行头）；
        # bbox 缺失的块沉底（并列时 min 取阅读顺序首个）
        def _top(b):
            bb = b.get("bbox")
            return bb[1] if bb else 9.0
        best = min(cands, key=_top)
        if best.get("type") == "text" and best.get("bbox") \
                and _top(best) > 0.45:
            continue  # 纯文本晋升要求页顶区（≤45% 页高），深位文本块宁可不锁
        best["type"] = "title"
        best["_anchored"] = True
        best["_pos_anchor"] = _normalize_title(text)
        if mnum:
            best["_pos_num"] = mnum.group(0)   # 供 stage3 补裸章题的章号
        best["level"] = level
        n += 1
    if n:
        logger.info(f"  泛名书签位置锚定: {n} 个 'Chapter N' 式条目"
                    f"按书签页锁定标题块")
    return n


def _rescue_by_page(blocks: list, toc_entries: list) -> int:
    """按页码定位救援未锚上的目录条目（锚点文本匹配全失败时）。

    两类典型场景：
    1. 章扉页竖排/美术字标题被上游 OCR 整块漏识别 → 无块可锚，
       按目录文本合成标题块插到预期页；
    2. 标题块存在，但目录页 OCR 错字超出文本模糊阈（如 卖淫女→奕淫女，
       3 字标题容不得容错）→ 用"页码位置 + 小编辑距离"联合证据晋升。

    偏移估计：已锚定标题的 (扫描页 - 印刷页) 投票。扫描件可能丢印刷页
    （全书偏移缓慢变化），故取**局部偏移**——印刷页码最近邻锚点的偏移；
    全局众数仅用于剔除封面残片之类的野票（±5 页以外）。
    **众数少于 3 票直接放弃救援**：偏移票全部互不相同是目录页码被
    LLM 瞎编的典型特征（数字与条目分离的目录版式），此时页码救援
    只会在错误位置造出幻影标题块，污染整棵树。

    顶层条目（章）额外加"下一个已锚定子条目的前一页"候选（章扉页通常
    紧邻首个节）。合成块只插稀疏页（≤4 块，像章扉页），稠密页宁可不救，
    避免标题插进上一章末尾污染两章。节级条目只晋升已有块，不合成。
    """
    anchors = _build_anchors(toc_entries)
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]
    if not anchors or not titled:
        return 0

    # ── 偏移投票（两遍：先众数，再剔野票） ──
    raw_votes = []  # (印刷页, 扫描页)
    for b in titled:
        m = _match_anchor((b.get("content") or "").strip(), anchors)
        if m and m[3] and b.get("level") == m[1]:
            raw_votes.append((m[3], b["page"]))
    if len(raw_votes) < 3:
        return 0
    mode, mode_n = Counter(pdf - pr for pr, pdf in raw_votes).most_common(1)[0]
    if mode_n < 3:
        logger.info(f"  页码救援放弃: 偏移票分散（众数仅 {mode_n} 票），"
                    f"目录页码不可信")
        return 0
    votes = sorted((pr, pdf) for pr, pdf in raw_votes
                   if abs(pdf - pr - mode) <= 5)
    if len(votes) < 3:
        return 0

    def offset_at(printed: int) -> int:
        pr, pdf = min(votes, key=lambda v: abs(v[0] - printed))
        return pdf - pr

    # ── 已满足条目（文本匹配 + 位置 sanity，防封面残片毒化） ──
    satisfied = {}  # anchor key → 匹配块页码
    for b in titled:
        text = (b.get("content") or "").strip()
        m = _match_anchor(text, anchors)
        if not m:
            continue
        # 完整键精确命中 = 强证据（封面残片只是前缀/短匹配），不看页码；
        # 否则要求块位置与"印刷页 + 局部偏移"吻合（±3 页）
        if m[3] is None or _normalize_title(text) == m[0] \
                or abs(b["page"] - (m[3] + offset_at(m[3]))) <= 3:
            satisfied.setdefault(m[0], b["page"])

    top_level = min(a[1] for a in anchors)
    max_page = max(b.get("page", 0) for b in blocks)
    page_block_count = Counter(b.get("page", 0) for b in blocks)
    page_blocks = {}
    for b in blocks:
        page_blocks.setdefault(b.get("page", 0), []).append(b)
    max_id = max((b.get("id", 0) for b in blocks), default=0)
    rescued = 0

    for i, (key, level, display, page) in enumerate(anchors):
        if key in satisfied:
            continue
        limit = 1 if len(key) < 12 else 2
        # 候选页：条目页码（局部偏移）+ 章级条目"下一锚定子条目前一页"
        candidates = set()
        if page:
            candidates.add(page + offset_at(page))
        if level == top_level and i + 1 < len(anchors):
            nxt = anchors[i + 1]
            if nxt[0] in satisfied:
                candidates.add(satisfied[nxt[0]] - 1)
        for pdf_page in sorted(candidates):
            if pdf_page < 1 or pdf_page > max_page:
                continue
            # 页码位置 + 文本模糊 联合证据：候选页上找与条目近似的块
            near_blocks = page_blocks.get(pdf_page, [])
            best = None  # (dist, is_title, block)
            for b in near_blocks:
                bkey = _normalize_title(b.get("content") or "")
                if not bkey or len(bkey) > 64:
                    continue
                d = _edit_distance_le(bkey, key, limit) \
                    if len(bkey) >= 3 else limit + 1
                if d <= limit:
                    cand = (d, 0 if b.get("type") == "title" else 1, b)
                    if best is None or cand[:2] < best[:2]:
                        best = cand
            if best is not None:
                b = best[2]
                if b.get("level", -1) <= 0:
                    b["type"] = "title"
                    b["level"] = level
                    b["rescued"] = "page_fuzzy"
                    b["_anchored"] = True
                    titled.append(b)
                    rescued += 1
                    logger.info(f"    页码救援: P{pdf_page} L{level} "
                                f"{(b.get('content') or '')[:30]}")
                satisfied[key] = pdf_page
                break
            # 无近似块：仅章级条目合成标题块，且只插稀疏页
            if level != top_level:
                continue
            near = [b for b in titled
                    if b["page"] in (pdf_page - 1, pdf_page, pdf_page + 1)]
            if any(b["level"] <= top_level
                   or _edit_distance_le(
                       _normalize_title(b.get("content") or ""),
                       key, limit) <= limit
                   for b in near):
                break  # 标题其实在（可能没锚上），不重复造块
            if page_block_count.get(pdf_page, 0) > 4:
                continue  # 稠密页不像章扉页，试下一个候选
            # id 取阅读顺序上的中间值（tree.py 用 id 大小代表先后顺序），
            # bbox 取页首条带（仅作位置元数据）
            pos = next((j for j, b in enumerate(blocks)
                        if b.get("page", 0) >= pdf_page), len(blocks))
            prev_id = blocks[pos - 1]["id"] if pos > 0 else 0
            succ_id = blocks[pos]["id"] if pos < len(blocks) else max_id + 1
            block = {
                "type": "title",
                "content": display,
                "bbox": [0.0, 0.0, 1.0, 0.05],
                "page": pdf_page,
                "id": (prev_id + succ_id) / 2,
                "level": level,
                "contd": -1,
                "image": -1,
                "rescued": "toc_page",
                "_anchored": True,
            }
            blocks.insert(pos, block)
            page_block_count[pdf_page] = page_block_count.get(pdf_page, 0) + 1
            titled.append(block)
            satisfied[key] = pdf_page
            rescued += 1
            logger.info(f"    页码回补: P{pdf_page} L{level} {display[:30]}")
            break
    if rescued:
        logger.info(f"  页码救援/回补: 共 {rescued} 处"
                    f"（全局偏移 {mode:+d}, {len(votes)} 票）")
    return rescued


def _detect_toc_pages(popo_titles: list, min_density: int = 6) -> set:
    """检测目录页：标题密度异常高的前部页。

    详目页每页常有 10-40 个标题条目，正文页一般只有 1-3 个。
    只看全书前 25%（目录几乎不会更靠后）。
    """
    from collections import Counter
    cnt = Counter(p for p, _lv, _t in popo_titles)
    if not cnt:
        return set()
    limit = max(20, max(cnt) // 4)
    return {p for p, n in cnt.items() if n >= min_density and p <= limit}


def _fix_front_matter_toc(front_matter: list, toc_pages: set) -> None:
    """用检测到的目录页修正 front_matter 的 toc 条目页码范围。

    DeepSeek 轻量兜底只采样书首若干页，长目录（如 12 页详目）的尾部
    可能超出其估计，导致目录条目漏进正文。就地修正。
    """
    if not toc_pages:
        return
    toc_entries = [f for f in front_matter if f.get("type") == "toc"]
    if toc_entries:
        for f in toc_entries:
            f["page_end"] = max(f.get("page_end", 0), max(toc_pages))
        logger.info(f"  目录页检测: 扩展到第 {max(toc_pages)} 页")
    else:
        front_matter.append({
            "type": "toc", "label": "目录",
            "page_start": min(toc_pages), "page_end": max(toc_pages),
            "keep": False,
        })
        logger.info(f"  目录页检测: 补充 toc 条目 第 {min(toc_pages)}-{max(toc_pages)} 页")


def _drop_duplicate_pages(blocks: list) -> list:
    """检测并丢弃重页（源 PDF 同一页被扫描两次）的 block。

    扫描本常见缺陷：同一页被重复扫描，管线会忠实地把两份都渲染出来，
    造成整章内容重复。
    判定：页文本 8 字 shingle 的 Jaccard 相似度 > 0.8（比较 N 与 N+1、N+2 页），
    丢弃后出现的那个（重扫描页通常靠后）。
    """
    page_text = {}
    for b in blocks:
        t = (b.get("content") or "").strip()
        if t:
            page_text[b["page"]] = page_text.get(b["page"], "") + re.sub(
                r"\s+", "", t)

    def shingles(s: str) -> set:
        return {s[i:i + 8] for i in range(0, max(len(s) - 7, 1))}

    pages = sorted(page_text)
    shingle_map = {p: shingles(page_text[p]) for p in pages}
    drop = set()
    for i, p in enumerate(pages):
        if p in drop:
            continue
        for q in pages[i + 1:i + 3]:
            if q in drop:
                continue
            a, c = shingle_map[p], shingle_map[q]
            if not a or not c:
                continue
            jaccard = len(a & c) / len(a | c)
            if jaccard > 0.8:
                drop.add(q)
                logger.info(f"  重页检测: P{q} 与 P{p} 重复 (相似度 {jaccard:.2f})，丢弃")

    if not drop:
        return blocks
    return [b for b in blocks if b["page"] not in drop]


_GLOBAL_LEVEL_PROMPT = """你是一位图书结构编辑。以下是一本书【{book}】的全部候选标题，按阅读顺序排列，每行用制表符分隔：ID、页码、编号形状、锚定、文本。

"锚定"列：L数字锁 = 目录真值层级（已锁定，无需你判断）；— = 需要你定级。

定级规则：
1. 编号形状相同的标题通常同级（第X章同级、一、同级、（一）同级、1.同级、数字开头的讲次同级）
2. 无编号标题一般是所属章/节内的小标题，层级应深于前面最近的章节级标题
3. 同一编号序列应连续且同级（1. 2. 3. 中间不应跳到别的层级）
4. 层级从 1 开始（最高层），逐级递增，相邻标题的层级跳跃通常不超过 1
5. 前言/序言/导论/后记/参考文献/索引/附录与章同级（通常为 1）
6. 保守原则：拿不准给更深一级，不要给更浅

输出 JSON 数组，只含无锚标题：[[ID, 层级], ...]，不要输出任何解释。

=== 候选标题表 ===
{table}"""


def _apply_global_levels(rows: list, lv_map: dict, floors: dict) -> int:
    """把全局定级结果应用到无锚标题（纯函数，便于测试）。

    rows: 候选标题块；lv_map: {id: level}；floors: {id(block): 下沉底线}。
    锚定块锁死；无编号标题受下沉底线钳制；层级钳制在 1..8。
    """
    n = 0
    for b in rows:
        if b.get("_anchored"):
            continue
        lv = lv_map.get(b.get("id"))
        if lv is None:
            continue
        try:
            lv = max(1, min(8, int(lv)))
        except (TypeError, ValueError):
            continue
        floor = floors.get(id(b))
        if floor and _title_shape((b.get("content") or "").strip()) == "plain":
            lv = max(lv, floor)
        if lv != b["level"]:
            b["level"] = lv
            n += 1
    return n


def _global_level_pass(blocks: list, book_name: str) -> int:
    """全局一致性定级：确定性规则把候选列表洗干净后，把全书标题表
    交给 LLM 一次定级（替代分块局部投票的漂移），锚点锁死校验。

    失败/解析异常 → 保持现有层级（确定性路径已可用），安全降级。
    """
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]
    if len(titled) < 10 or not DEEPSEEK_API_KEY:
        return 0

    # 每块的下沉底线（所属锚定章 +1）
    floors = {}
    last_anchor = None
    for b in titled:
        if b.get("_anchored"):
            last_anchor = b["level"]
        floors[id(b)] = (last_anchor + 1) if last_anchor else None

    lines = []
    for b in titled:
        shape = _title_shape((b.get("content") or "").strip())
        anchor = f"L{b['level']}锁" if b.get("_anchored") else "—"
        lines.append(f"{b.get('id')}\t{b.get('page')}\t{shape}\t{anchor}"
                     f"\t{(b.get('content') or '').strip()[:40]}")

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = chat_create(
            client,
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": _GLOBAL_LEVEL_PROMPT.format(
                book=book_name, table="\n".join(lines))}],
            max_tokens=8192,
            temperature=0.1,
        )
        data = json.loads(_clean_json_array_response(
            resp.choices[0].message.content or ""))
        lv_map = {int(i[0]): i[1] for i in data
                  if isinstance(i, list) and len(i) >= 2}
    except Exception as e:
        logger.warning(f"  全局定级失败: {e}，保持现有层级")
        return 0

    n = _apply_global_levels(titled, lv_map, floors)
    if n:
        logger.info(f"  全局定级: {n} 个无锚标题由 LLM 全局一致性调整")
    return n


# ── 前后页条目词表锚定（病例 028）──────────────────────────────
# outline 先验弃用后，'前言/序/版权页/后记' 这类几乎每本书都有、印刷目录又常
# 不收的条目由词表规则锚定：归一化全等（或 词表词+括号署名，'前言（贾彦德）'）
# 且处于前/后页区即锁 L1。词表刻意保守——导言/Introduction/附录/参考文献/
# 目录 是结构标题或自指条目，不在此列（铁律 0：拿不准就不动作）。

_FRONT_LEXICON_CN = (
    "前言", "序言", "序章", "序", "前记", "再版前言", "再版前记", "自序",
    "原版前言", "初版前言", "修订版前言", "作者简介", "作者介绍",
    "版权页", "版权信息", "版权", "扉页", "献词", "致谢", "鸣谢",
    "凡例", "出版说明", "编者的话", "内容提要", "内容简介",
)
_BACK_LEXICON_CN = (
    "后记", "跋", "出版后记", "再版后记", "致谢", "鸣谢", "作者简介", "作者介绍",
)
_FRONT_LEXICON_EN = (
    "foreword", "preface", "prologue", "acknowledgments", "acknowledgements",
    "about the author", "copyright", "copyright page", "dedication",
)
_BACK_LEXICON_EN = (
    "epilogue", "afterword", "acknowledgments", "acknowledgements",
    "about the author",
)


def _fm_lexicon_zone(blocks: list) -> tuple[int, int]:
    """前/后页区边界（扫描页码）：首个/末个章节编号形状（第X章/Chapter N）
    标题块的页码；无章号书回退 前 25 页 / 全书 80% 处（词表条目本就出现在头尾）。"""
    chap_pages = [b.get("page", 0) for b in blocks
                  if b.get("type") == "title"
                  and _title_shape((b.get("content") or "").strip()) in ("chap_cn", "chap_en")]
    max_page = max((b.get("page", 0) for b in blocks), default=0)
    front_end = min(chap_pages) if chap_pages else 25
    back_start = max(chap_pages) if chap_pages else int(max_page * 0.8)
    return front_end, back_start


def _fm_lexicon_match(text: str, front: bool) -> bool:
    """词表命中判定：归一化全等，或 '词表词+括号署名'（'前言（贾彦德）'）。
    空串/超 25 字符的块不收（真词表条目都是短行）。"""
    raw = (text or "").strip()
    if not raw or len(raw) > 25:
        return False
    norm = _normalize_title(raw)
    if not norm:
        return False
    lexicon = (_FRONT_LEXICON_CN + _FRONT_LEXICON_EN) if front \
        else (_BACK_LEXICON_CN + _BACK_LEXICON_EN)
    for w in lexicon:
        wn = _normalize_title(w)
        if norm == wn:
            return True
        if re.fullmatch(re.escape(wn) + r"[（(].*[）)]", norm):
            return True
    return False


def _anchor_frontmatter_lexicon(blocks: list, toc_pages: set | None = None) -> int:
    """前后页词表锚定：命中的块锁 L1；text 块（引擎丢 text_level 的形态）晋升 title。

    词表命中本身就是强证据，不依赖目录条目。在全部层级校正之后调用
    （下沉/全局定级不再覆盖）。失败方向=不动作：
    - 不在词表/不在页区一律不碰；
    - 目录页上的"条目罗列行"不碰（toc_pages 跳过）；
    - 同页 ≥2 个词表命中整页跳过（真实前/后页一页一条；一页多条 = 目录页
    //罗列形态——BAC 实测：Contents 页把 Dedication/Acknowledgments/About
    the Author 同时列出，词表逐个命中会锚出幻影条目）。
    """
    front_end, back_start = _fm_lexicon_zone(blocks)
    toc_pages = toc_pages or set()
    hits: list[tuple[dict, bool]] = []
    for b in blocks:
        if b.get("type") not in ("title", "text"):
            continue
        page = b.get("page", 0)
        if page in toc_pages:
            continue
        text = (b.get("content") or "").strip()
        if page <= front_end and _fm_lexicon_match(text, front=True):
            hits.append((b, True))
        elif page >= back_start and _fm_lexicon_match(text, front=False):
            hits.append((b, False))
    from collections import Counter as _Counter
    page_hits = _Counter(b.get("page", 0) for b, _ in hits)
    n = 0
    for b, _front in hits:
        if page_hits[b.get("page", 0)] >= 2:
            continue  # 同页多条命中 = 罗列页形态，整页不动作
        b["type"] = "title"
        if b.get("level", -1) != 1:
            b["level"] = 1
        b["_fm_rule"] = True
        n += 1
    if n:
        logger.info(f"  前后页词表锚定: {n} 个条目锁 L1（前言/序/版权页/后记等）")
    return n


def finish_structure(blocks: list, content_list: list, book_name: str,
                     work_dir: str, engine: str, progress=None,
                     pdf_toc: list | None = None) -> dict:
    """共享收尾：标注 blocks → 重页丢弃 → 轻量兜底 → 目录修正 → 锚定校正 → 文档树。

    pdf_toc: PDF outline/书签转成的 toc_entries（确定性元数据，born-digital
    PDF 的免费真值），非空时取代 LLM 提取的目录作为最高优先级先验。
    ⚠️ 病例 028 起 outline 先验已弃用（pipeline 恒传 None；扫描本第三方书签
    形态不可控——页码书签洪水/泛名条目/假目录三连事故），只信 OCR 重建目录
    + 前后页词表锚定。此参数与 _sanitize_pdf_toc 等链路保留备查。
    """
    _report = progress or (lambda *a, **kw: None)
    work_dir = Path(work_dir)

    # 重页丢弃（源 PDF 重复扫描页防重）
    blocks = _drop_duplicate_pages(blocks)

    # 标注统计
    n_contd = sum(1 for b in blocks if b.get("contd", -1) >= 0)
    n_titled = sum(1 for b in blocks if b.get("level", -1) > 0)
    n_linked = sum(1 for b in blocks if b.get("image", -1) >= 0)
    n_tmerge = sum(1 for b in blocks if b.get("table_merge", -1) >= 0)
    logger.info(f"    跨页拼接 {n_contd} 处, 标题 {n_titled} 个, "
                f"图文关联 {n_linked} 处, 表格合并 {n_tmerge} 处")

    # ── DeepSeek 轻量兜底（metadata + 前后页 + 目录条目） ──
    popo_titles = [
        (b["page"], b["level"], (b.get("content") or "").strip())
        for b in blocks if b.get("level", -1) > 0
    ]
    light = _light_metadata_pass(content_list, book_name,
                                 popo_titles=popo_titles, progress=_report)

    # PDF outline/书签是确定性元数据（born-digital PDF 的免费真值），
    # 优先级高于 LLM 从目录页提取/编造的 toc_entries；但扫描本自制书签
    # 可能是'标题／页码'假目录，先过清洗闸门
    if pdf_toc:
        pdf_toc = _sanitize_pdf_toc(pdf_toc)
    if pdf_toc:
        pdf_toc = _normalize_generic_outline_levels(pdf_toc)
        logger.info(f"  PDF outline 先验: {len(pdf_toc)} 条书签目录"
                    f"（取代 LLM toc_entries）")
        light["toc_entries"] = pdf_toc

    # ── 规则目录兜底（病例 029/030）：LLM 交付 0 条但本地探到目录页 →
    # 从目录页规则提取（采样未覆盖的残余漏网 + 端点内容过滤拒答的最后一道网；
    # 只在"LLM 空手"时启动，失败方向=不动作，与无目录书同路径） ──
    if not light.get("toc_entries"):
        _pages_map = _page_texts(content_list)
        _toc_p = _find_toc_page(_pages_map)
        if _toc_p is not None:
            rule_entries = _rule_toc_extract(_pages_map, _toc_p)
            if rule_entries:
                logger.info(f"  规则目录兜底: LLM 提取为空，"
                            f"规则从 P{_toc_p} 起提取 {len(rule_entries)} 条")
                light["toc_entries"] = rule_entries

    # 目录页码修复（页码与条目分离的版式下 LLM 页码不可信，按 y 对齐重配）
    light["toc_entries"], toc_pages = _repair_toc_pages(
        light.get("toc_entries", []), content_list)
    # 目录页识别补集：blob 合并块/简目等无独立数字块的形态按条目行命中识别
    toc_pages |= _detect_toc_pages_by_entries(
        light.get("toc_entries", []), content_list)
    # 章首 mini-TOC 误伤防护：目录页降格只作用于书首全局目录页（≤25 页）。
    # 章首 mini-TOC 与全局目录共享条目形态（≥3 条目行+页码数字块），
    # 会被识别器一并标为目录页，真章题随降格消失、运行头被救援顶替
    # （病例 QFT ch4：p98 被标为目录页 → 真章题变正文，
    # 下一页运行头晋升顶替——"电影播了十分钟才标开头"）
    toc_pages = {p for p in toc_pages if p <= 25}

    # ── 伪造目录硬兜底 ──
    # 书里没有目录页时，LLM 不会返回空 toc_entries，而是拿附带的"全书标题
    # 列表"编造一份假目录（page 直接抄标题块的扫描页码）。假条目经锚点
    # 以最高优先级锁死错误层级，形状栈/字号阶梯全部跳过，结构散架。
    # 判定一（主）：两个目录页识别器都没找到目录页 → 判定无印刷目录；
    # 判定二（双保险）：≥80% 条目的 page 与对应标题块扫描页完全相等
    # （真目录给印刷页，与扫描页通常有非零偏移；全等=抄了标题列表页码）。
    # PDF outline 先验是确定性元数据，不参与伪造判定。
    if light["toc_entries"] and not pdf_toc:
        if not toc_pages:
            logger.warning(
                f"  未检测到目录页，丢弃 LLM toc_entries"
                f"（{len(light['toc_entries'])} 条，可能为伪造），"
                f"改用形状栈+编号先验")
            light["toc_entries"] = []
        else:
            n_cmp, n_eq = _forged_toc_fingerprint(
                light["toc_entries"], blocks)
            if n_cmp >= 5 and n_eq >= 0.8 * n_cmp:
                logger.warning(
                    f"  目录指纹疑似伪造（{n_eq}/{n_cmp} 条目页码与标题块"
                    f"扫描页完全相等），丢弃 toc_entries，改用形状栈+编号先验")
                light["toc_entries"] = []

    # 目录页本地检测，修正 front_matter 的 toc 边界（长目录防漏）
    _fix_front_matter_toc(light["front_matter"], _detect_toc_pages(popo_titles))

    # ── TOC 锚定校正（校准模型的漂移层级） ──
    _report("TOC 锚定校正层级...")
    _calibrate_levels(blocks, light.get("toc_entries", []),
                      toc_pages={p + 1 for p in toc_pages})

    # ── 泛名书签位置锚定（'Chapter N' 式 outline 条目，文本锚定全灭时
    # 按书签页锁定标题块；非 born-digital/无泛名条目时为空操作） ──
    _anchor_generic_outline(blocks, light.get("toc_entries", []))

    # ── 页码救援/回补未锚上的目录条目（章扉页被 OCR 整块漏识别等） ──
    _rescue_by_page(blocks, light.get("toc_entries", []))

    # ── 锚点身份查重 + 无锚垃圾标题否决（救援后、下沉前） ──
    _dedup_anchored_titles(blocks, light.get("toc_entries", []))
    _veto_junk_titles(blocks, light.get("toc_entries", []))

    # ── 无编号无锚标题下沉（救援完成后执行：系列块如'答学友问2..12'
    # 依赖救援先锚定系列首项'答学友问1'，否则会错误沉到上一章内） ──
    _sink_unanchored_plain(blocks)

    # ── 全局一致性定级（LLM 看全书标题表统一定级，锚点锁死校验；
    # 分块局部投票的漂移在此收口；失败自动保持现有层级） ──
    if GLOBAL_LEVEL_PASS:
        _report("LLM 全局一致性定级...")
        _global_level_pass(blocks, book_name)

    # ── 前后页词表锚定（病例 028：前言/序/版权页/后记等，outline 弃用后
    # 的免费真值；最后执行，层级不再被任何后续机制覆盖。目录页跳过——
    # 词表条目在目录页上是罗列行不是标题；toc_pages 此处为 0 起页码） ──
    _anchor_frontmatter_lexicon(blocks, toc_pages={p + 1 for p in toc_pages})

    blocks_path = work_dir / "popo_blocks.json"
    with open(blocks_path, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False)

    # ── 建文档树（用校正后的层级） ──
    _report("构建文档树...")
    tree = popo.build_tree(blocks)

    return {
        "engine": engine,
        "metadata": light["metadata"],
        "front_matter": light["front_matter"],
        "back_matter": light["back_matter"],
        "noise_ranges": [],
        "tree": tree,
        "toc_entries": light.get("toc_entries", []),
        # outline 来源标记：stage3 的泛名条目正文起点回收只信书签页码
        # （扫描页真值）；LLM 从印刷目录提取的条目是印刷页，不可用于回收
        "toc_source": "outline" if pdf_toc else None,
        "popo_blocks_file": blocks_path.name,
    }


__all__ = ["finish_structure", "save_structure", "_calibrate_levels"]
