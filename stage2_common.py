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

logger = logging.getLogger(__name__)

# DeepSeek 轻量兜底采样参数
_FRONT_PAGES = 15   # 书首采样页数
_BACK_PAGES = 5     # 书尾采样页数
_PAGE_CHARS = 800   # 每页最多采样字符

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
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
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


def _normalize_title(text: str) -> str:
    """标题归一化：剥脚注上标/装饰前缀 + 去 $ 定界符和所有空白 + 大小写折叠，
    用于目录条目匹配。'$' 是数学定界符，目录与正文的公式块常差一层 $$
    包裹（'一、$f(x)=..$型' vs '$$ 一、f(x)=.. 型 $$'），剥掉才对齐。
    （'FOREWORD' 应能匹配 'Foreword: François Ewald …' 前缀；
    中文无大小写，不受影响）"""
    t = _SUP_MARK_RE.sub("", text or "")
    t = _DECOR_PREFIX_RE.sub("", t.strip())
    t = t.replace("$", "")
    # LaTeX 格式命令是纯排版噪声（目录与正文常不一致）
    t = t.replace("\\left", "").replace("\\right", "")
    # 弯引号/弯撇号统一为直引（OCR 与目录常不一致）
    t = (t.replace("’", "'").replace("‘", "'")
           .replace("“", '"').replace("”", '"'))
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
    ("num_paren", re.compile(r"^[（(]?\d+[）)]")),
    ("roman", re.compile(r"^[IVXLCDM]+[.、]\s")),
    ("alpha", re.compile(r"^[a-zA-Z][.、]\s")),
]


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
_TRAIL_PAGE_RE = re.compile(r"[\s.…·_/—–]+\d+\s*$")


def _strip_trailing_page(text: str) -> str:
    return _TRAIL_PAGE_RE.sub("", (text or "").strip()).strip()


def _build_anchors(toc_entries: list) -> list:
    """目录条目 → 锚点表 [(归一化键, level, 显示文本, 印刷页码|None)]

    剥离尾部页码（"第一章 …… 23"、"xxx / 060"）之外，若剥离改变了文本，
    **同时保留完整形态**：标题本身以数字结尾时（"one 7 JANUARY 1976"），
    剥尾会把年份吃掉，导致正文标题永远锚不上。完整形态在前，精确命中优先取它。
    """
    anchors = []
    for e in toc_entries or []:
        raw = (e.get("text") or "").strip()
        if not raw:
            continue
        display = _strip_trailing_page(raw)
        full_key = _normalize_title(raw)
        key = _normalize_title(display)
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
        if key and key != full_key:
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
    prefix_best = None
    suffix_best = None
    long_prefix_best = None
    for a in anchors:
        k, lv = a[0], a[1]
        if k == key:
            return a
        if k.startswith(key) and len(k) > len(key):
            # 块是锚点的前缀（"第一章" → "第一章 民法概念论"），取最长
            if prefix_best is None or len(k) > len(prefix_best[0]):
                prefix_best = (k, a)
        elif len(key) >= 3 and k.endswith(key) and len(k) > len(key):
            # 块是锚点的尾部（"权利主体" → "第二编 权利主体"、
            # "RUN" → "Chapter 1: Run"），取最短
            if suffix_best is None or len(k) < len(suffix_best[0]):
                suffix_best = (k, a)
        elif len(k) >= 6 and key.startswith(k) and len(key) > len(k):
            # 锚点是块的前缀（目录条目被截断，如 '9.2.1 Kau' →
            # '9.2.1 Kauzmann paradox'；限长锚点防 '1.1' 误配 '1.1.2'）
            if long_prefix_best is None or len(k) > len(long_prefix_best[0]):
                long_prefix_best = (k, a)
    if prefix_best is not None:
        return prefix_best[1]
    if suffix_best is not None:
        return suffix_best[1]
    if long_prefix_best is not None:
        return long_prefix_best[1]
    # 块是锚点的子串（副标题被 OCR 截断，如"…——当代新"缺尾字）；
    # 限长块防"权利主体"式短块错配，取最短包含锚点（最具体）
    if len(key) >= 8:
        sub_best = None
        for a in anchors:
            k = a[0]
            if key in k and len(k) > len(key):
                if sub_best is None or len(k) < len(sub_best[0]):
                    sub_best = (k, a)
        if sub_best is not None:
            return sub_best[1]
    # 模糊兜底：目录页与正文的 OCR 结果常有单字差异（僵/催、是/和、缺字）
    if len(key) >= 4:
        best_dist = 3
        best = None
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
            if d < best_dist:
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
    # 与目录条目精确匹配的短文本块，按目录定义强制晋升为标题。
    # 长度上限 64：含公式的节标题会超过 40（数学书 '一、f(x)=e^{λx}P_m(x)型'）
    rescued = 0
    for b in blocks:
        if b.get("type") == "title" and b.get("level", -1) > 0:
            continue
        if b.get("page") in toc_pages:
            continue
        text = (b.get("content") or "").strip()
        key = _normalize_title(text)
        if not key or len(key) > 64:
            continue
        lv = match_anchor(text)
        if lv is not None:
            b["type"] = "title"
            b["level"] = lv
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
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": _GLOBAL_LEVEL_PROMPT.format(
                book=book_name, table="\n".join(lines))}],
            max_tokens=8192,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
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


def finish_structure(blocks: list, content_list: list, book_name: str,
                     work_dir: str, engine: str, progress=None,
                     pdf_toc: list | None = None) -> dict:
    """共享收尾：标注 blocks → 重页丢弃 → 轻量兜底 → 目录修正 → 锚定校正 → 文档树。

    pdf_toc: PDF outline/书签转成的 toc_entries（确定性元数据，born-digital
    PDF 的免费真值），非空时取代 LLM 提取的目录作为最高优先级先验。
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
    # 优先级高于 LLM 从目录页提取/编造的 toc_entries
    if pdf_toc:
        logger.info(f"  PDF outline 先验: {len(pdf_toc)} 条书签目录"
                    f"（取代 LLM toc_entries）")
        light["toc_entries"] = pdf_toc

    # 目录页码修复（页码与条目分离的版式下 LLM 页码不可信，按 y 对齐重配）
    light["toc_entries"], toc_pages = _repair_toc_pages(
        light.get("toc_entries", []), content_list)
    # 目录页识别补集：blob 合并块/简目等无独立数字块的形态按条目行命中识别
    toc_pages |= _detect_toc_pages_by_entries(
        light.get("toc_entries", []), content_list)

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

    # ── 页码救援/回补未锚上的目录条目（章扉页被 OCR 整块漏识别等） ──
    _rescue_by_page(blocks, light.get("toc_entries", []))

    # ── 无编号无锚标题下沉（救援完成后执行：系列块如'答学友问2..12'
    # 依赖救援先锚定系列首项'答学友问1'，否则会错误沉到上一章内） ──
    _sink_unanchored_plain(blocks)

    # ── 全局一致性定级（LLM 看全书标题表统一定级，锚点锁死校验；
    # 分块局部投票的漂移在此收口；失败自动保持现有层级） ──
    if GLOBAL_LEVEL_PASS:
        _report("LLM 全局一致性定级...")
        _global_level_pass(blocks, book_name)

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
        "popo_blocks_file": blocks_path.name,
    }


__all__ = ["finish_structure", "save_structure", "_calibrate_levels"]
