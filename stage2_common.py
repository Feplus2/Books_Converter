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
from pathlib import Path

from openai import OpenAI

import popo
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
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
   印刷页码（整数），条目本身不带页码则填 null
6. 所有页码用整数，language 用两位小写代码
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

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": _LIGHT_PROMPT.format(
                front=len(front), back=len(back), sample=sample,
                titles=titles_text)}],
            max_tokens=8192,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = resp.choices[0].message.content
        result = json.loads(_clean_json_response(raw))

        # 校验/补全
        result.setdefault("metadata", {})
        result["metadata"].setdefault("title", book_name)
        result["metadata"].setdefault("language", "zh")
        result.setdefault("front_matter", [])
        result.setdefault("back_matter", [])
        result.setdefault("toc_entries", [])
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

    except Exception as e:
        logger.warning(f"  DeepSeek 轻量兜底失败: {e}，使用降级 metadata")
        return _fallback_metadata(book_name)


def _normalize_title(text: str) -> str:
    """标题归一化：去所有空白，用于目录条目匹配"""
    return re.sub(r"[\s　]+", "", text or "").strip()


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


def _build_anchors(toc_entries: list) -> list:
    """目录条目 → 锚点表 [(归一化键, level, 显示文本, 印刷页码|None)]"""
    anchors = []
    for e in toc_entries or []:
        raw = (e.get("text") or "").strip()
        if not raw:
            continue
        display = re.sub(r"[\s.…·_]+\d+\s*$", "", raw).strip()
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
        if key and level > 0:
            anchors.append((key, level, display, page))
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
    for a in anchors:
        k, lv = a[0], a[1]
        if k == key:
            return a
        if k.startswith(key) and len(k) > len(key):
            # 块是锚点的前缀（"第一章" → "第一章 民法概念论"），取最长
            if prefix_best is None or len(k) > len(prefix_best[0]):
                prefix_best = (k, a)
        elif len(key) >= 4 and k.endswith(key) and len(k) > len(key):
            # 块是锚点的尾部（"权利主体" → "第二编 权利主体"），取最短
            if suffix_best is None or len(k) < len(suffix_best[0]):
                suffix_best = (k, a)
    if prefix_best is not None:
        return prefix_best[1]
    if suffix_best is not None:
        return suffix_best[1]
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
    if len(key) >= 6:
        limit = 1 if len(key) < 12 else 2
        best_dist = limit + 1
        best = None
        ambiguous = False
        for a in anchors:
            d = _edit_distance_le(key, a[0], limit)
            if d < best_dist:
                best_dist, best, ambiguous = d, a, False
            elif d == best_dist and a is not best:
                ambiguous = True
        if best is not None and not ambiguous:
            return best
    return None


def _calibrate_levels(blocks: list, toc_entries: list) -> int:
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

    # ── 锚点驱动的标题救援 ──
    # 编/章分隔页常被模型漏判（大字孤立、无上下文）；
    # 与目录条目精确匹配的短文本块，按目录定义强制晋升为标题。
    rescued = 0
    for b in blocks:
        if b.get("type") == "title" and b.get("level", -1) > 0:
            continue
        text = (b.get("content") or "").strip()
        key = _normalize_title(text)
        if not key or len(key) > 40:
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

    logger.info(f"  TOC 锚定校正: {hits} 个锚点命中（共 {len(titled)} 个标题）")
    return hits


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

    顶层条目（章）额外加"下一个已锚定子条目的前一页"候选（章扉页通常
    紧邻首个节）。合成块只插稀疏页（≤4 块，像章扉页），稠密页宁可不救，
    避免标题插进上一章末尾污染两章。节级条目只晋升已有块，不合成。
    """
    from collections import Counter

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
    mode = Counter(pdf - pr for pr, pdf in raw_votes).most_common(1)[0][0]
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
        m = _match_anchor((b.get("content") or "").strip(), anchors)
        if not m:
            continue
        if m[3] is None or abs(b["page"] - (m[3] + offset_at(m[3]))) <= 3:
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
                if not bkey or len(bkey) > 45:
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


def finish_structure(blocks: list, content_list: list, book_name: str,
                     work_dir: str, engine: str, progress=None) -> dict:
    """共享收尾：标注 blocks → 重页丢弃 → 轻量兜底 → 目录修正 → 锚定校正 → 文档树。"""
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

    # 目录页本地检测，修正 front_matter 的 toc 边界（长目录防漏）
    _fix_front_matter_toc(light["front_matter"], _detect_toc_pages(popo_titles))

    # ── TOC 锚定校正（校准模型的漂移层级） ──
    _report("TOC 锚定校正层级...")
    _calibrate_levels(blocks, light.get("toc_entries", []))

    # ── 页码救援/回补未锚上的目录条目（章扉页被 OCR 整块漏识别等） ──
    _rescue_by_page(blocks, light.get("toc_entries", []))

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
