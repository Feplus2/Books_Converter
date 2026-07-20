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
    {{"text": "目录条目的完整文字（如'第一章 蠢材的天堂'，不含页码和点线）", "level": 1}}
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
   （OCR 可能有少量错字，选择最合理的版本）
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
        if key and level > 0:
            anchors.append((key, level, display))

    def match_anchor(text: str):
        """归一化匹配：精确 > 块是锚点前缀 > 块是锚点尾部（分隔页模式）。

        不做"中间包含"匹配——会把 '权利主体' 错配到
        '第一节 作为权利主体的自然人' 这类更长条目上。
        """
        key = _normalize_title(text)
        if not key:
            return None
        prefix_best = None
        suffix_best = None
        for k, lv, _d in anchors:
            if k == key:
                return lv
            if k.startswith(key) and len(k) > len(key):
                # 块是锚点的前缀（"第一章" → "第一章 民法概念论"），取最长
                if prefix_best is None or len(k) > len(prefix_best[0]):
                    prefix_best = (k, lv)
            elif len(key) >= 4 and k.endswith(key) and len(k) > len(key):
                # 块是锚点的尾部（"权利主体" → "第二编 权利主体"），取最短
                if suffix_best is None or len(k) < len(suffix_best[0]):
                    suffix_best = (k, lv)
        if prefix_best is not None:
            return prefix_best[1]
        if suffix_best is not None:
            return suffix_best[1]
        return None

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
    for _k, lv, display in anchors:
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
        toc_level = match_anchor(text)

        if toc_level is not None:
            # 锚点锁定，并把栈重置到该层级
            while stack and stack[-1][1] >= toc_level:
                last_shape_level[stack[-1][2]] = stack[-1][1]
                stack.pop()
            b["level"] = toc_level
            stack.append((key, toc_level, shape))
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
