"""Stage 1 VLM 引擎：多模态模型逐页直读整本书 → content_list 契约。

设计依据 wiki/06-vlm-pipeline.md §8（T0–T6 实验全绿）：
- 转写/结构/脚注主力模型 GLM-5.3-Flash @ thinking low（脚注 A 案 JSON 直接配对）；
- 图片提取：转写模型出粗框（同 key，零额外配置）+ raster_snap 连通域
  光栅重裁收边（T4b 实测 meanIoU 0.883、IoU≥0.8 比例 95.2%）；
- 上页尾部 300 字符 <context_only> 注入（零续写污染实测）；
- 目录页就地提取、简目详目去重、层级提示注入后续页（形状栈：同级同类同 level）；
- SQLite 状态库逐页事务提交，断点续跑；页失败方向=不动作（留空+记录，
  由 QC 连续页覆盖检查兜底）。

产物（落盘 <work_dir>/vlm/，与 MinerU/PaddleOCR 契约一致）：
    {stem}_content_list.json / {stem}.md / images/ / vlm_state.db / metadata.json
"""
import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from vlm_client import VlmClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt（生产版：逐字纪律强化；实验版见 scripts/vlm_lab/prompts.py）
# ---------------------------------------------------------------------------

_PAGE_PROMPT = """你是高精度图书数字化引擎。输入一页书的扫描图像，输出一个 JSON 对象（不要任何其他文字、不要代码围栏）。

{prev_tail_block}{toc_hint_block}输出 schema：
{{
 "kind": "front | toc | body | blank",
 "printed_page": 页面上印的页码（整数；没有/无法辨认给 null）,
 "running_head": "页眉/书眉文字（没有给 null）",
 "blocks": [ ...按阅读顺序... ],
 "footnotes": [ {{"marker": "①", "text": "该标记对应的页底注文"}}, ... ],
 "has_image": true/false
}}

blocks 元素（t 为类型）：
 {{"t": "title", "text": "标题文字", "level": N}}   —— 层级：编/部分=1，章=2，节=3，小节=4，更细=5
 {{"t": "text",  "text": "正文段落文字"}}
 {{"t": "image", "caption": "图题/图注文字（如 图3-1 民法的法源），没有给空串"}}
目录页（kind="toc"）的 blocks 逐条输出：
 {{"t": "toc", "text": "条目标题（剥掉页码与点线）", "level": N, "page": 印刷页码整数或 null}}

铁律：
1. 逐字转写。绝对不得概括、补全、翻译、规范化或意译；专业术语、异体字、标点一律照页面原文。
2. 页眉、页码、页脚装饰线不要进 blocks（记入 running_head / printed_page 字段）。
3. **形状栈纪律：同一类标题必须同一 level**——所有"第X编"同级、所有"第X章"同级、
   所有"第X节"同级，全书任何一页都不得漂移。
4. 脚注：正文中的圈码（①②③…）原样保留在正文位置；页底注文进 footnotes，
   每条必须有 marker；正文中出现的每个圈码都必须有对应 footnote。
5. 插图/图表：不要转写图内文字，只给 image 块 + caption。
   **表格**：用 table 块输出，text 为 **markdown pipe 表格**（| 列1 | 列2 |…，
   首行表头、次行 |---|---| 分隔），caption 字段放表题（没有给空串）；
   不要跨行合并单元格标记，按可见网格逐行展开。
6. 完全空白页：{{"kind": "blank", "blocks": [], "footnotes": [], "has_image": false}}。
7. **公式纪律（数理工书关键）**：
   - 行内公式（嵌在句中的符号、变量、短式）→ 用 $…$ 包 LaTeX 写进正文 text，
     如 "其中 $\\psi_L$ 是左手场"。
   - 独立成行的公式 → 单独一个 blocks 元素 {{"t": "text", "text": "$$…LaTeX…$$ \\qquad (编号)"}}；
     公式编号（如 (3.121)、(1.25)）放在 $$ 内末尾、用 \\qquad 分隔；无编号则省略。
   - 公式一律用 LaTeX 表达，禁止 Unicode 数学符号平铺：√ 写 \\sqrt{{}}、∑ 写 \\sum、
     上下标用 ^{{}}/_{{}}、希腊字母写 \\psi、\\sigma。"""


_PREV_TAIL_TMPL = """<context_only do_not_transcribe>
以下是上一页的结尾，仅供理解跨页语境（断句、脚注编号延续）。
绝对不要转写或复述它；你的 blocks 只覆盖当前这一页图像：
{tail}
</context_only>

"""

_TOC_HINT_TMPL = """本书已确认的目录层级规则（必须遵守）：{scheme}。

"""

_IMAGE_BBOX_PROMPT = """这是一页书的扫描图像。请定位页面中所有插图/图表区域（非文字的图片、示意图、图表；不含页眉装饰线、不含纯文字表格），输出 JSON（不要其他文字）：
{"images": [{"bbox": [x1, y1, x2, y2]}, ...]}
坐标为归一化整数（0-1000 千分位），原点在页面左上角，x 向右 y 向下。没有插图则输出 {"images": []}。"""

# ---------------------------------------------------------------------------
# 状态库（薄 SQLite：pages + book_state，每页事务提交）
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages(
  page_idx INTEGER PRIMARY KEY,
  status TEXT NOT NULL,           -- ok / error
  raw_json TEXT,                  -- 模型输出（解析校验后的页 JSON）
  n_blocks INTEGER, n_footnotes INTEGER, has_image INTEGER,
  error TEXT, latency_s REAL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS book_state(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


class _StateDB:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get_page(self, page_idx: int):
        with self._lock:
            return self._conn.execute(
                "SELECT status, raw_json FROM pages WHERE page_idx=?", (page_idx,)).fetchone()

    def save_page(self, page_idx: int, status: str, raw: dict | None,
                  n_blocks: int, n_footnotes: int, has_image: bool,
                  error: str, latency: float | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?,?)",
                (page_idx, status, json.dumps(raw, ensure_ascii=False) if raw else None,
                 n_blocks, n_footnotes, int(has_image), (error or "")[:300] or None,
                 latency, time.strftime("%Y-%m-%d %H:%M:%S")))
            self._conn.commit()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM book_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO book_state VALUES(?,?)", (key, value))
            self._conn.commit()

    def failed_pages(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT page_idx FROM pages WHERE status='error'").fetchall()
        return sorted(r[0] for r in rows)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _reading_order(boxes: list) -> list:
    """行感知阅读序：先按 y 聚行（垂直重叠 ≥50% 即同行），行内按 x 排序。
    纯 y 主序在同行多图时被检测噪声翻转——高数 p24 的 2×2 图阵实测：
    图1-7/1-8 整列互换（y 微差 1-8 在前）→ caption 与图配对错位。"""
    rows: list[list] = []
    for b in sorted(boxes, key=lambda b: (b[1], b[0])):
        for row in rows:
            r0 = row[0]
            ov = min(b[3], r0[3]) - max(b[1], r0[1])
            if ov > 0.5 * min(b[3] - b[1], r0[3] - r0[1]):
                row.append(b)
                break
        else:
            rows.append([b])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: b[0]))
    return out


def _synth_bbox(i: int, n: int) -> list[int]:
    """合成 bbox（0-1000 千分位）：页内顺序 → 单调递增 y。下游位置语义只需保序。"""
    step = 860 / max(n, 1)
    y1 = int(70 + i * step)
    return [110, y1, 890, int(y1 + step * 0.62)]


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _needs_image_extract(pj: dict, images_dir) -> bool:
    """该页是否仍需图片提取：有 image 块缺 img_path，或 img_path 指向的 PNG 不在盘。

    全部就位 → False：断点续跑/重放不重烧 bbox API（img_path 已回写 db 后，
    回填的页面自带配对结果）。失败方向 = 不提取（图保持占位或既有配对），
    绝不因误判重提取而打乱既有配对。
    """
    from pathlib import Path as _P
    for b in pj.get("blocks", []):
        if b.get("t") != "image":
            continue
        ip = b.get("img_path")
        if not ip or not (_P(images_dir) / _P(str(ip)).name).exists():
            return True
    return False


def _head_candidates(pages_json: dict[int, dict], toc_entries: list) -> set:
    """书眉候选（归一化）：高频 running_head 值（≥3 页）+ 目录 L1/L2 条目文本。"""
    cands = set()
    freq: dict[str, int] = {}
    for pj in pages_json.values():
        rh = str(pj.get("running_head") or "").strip()
        if rh:
            k = _norm_key(rh)
            freq[k] = freq.get(k, 0) + 1
    for k, n in freq.items():
        if n >= 3:
            cands.add(k)
    for e in toc_entries:
        if isinstance(e.get("level"), int) and e["level"] <= 2:
            cands.add(_norm_key(e.get("text", ""))[:40])
    return {c for c in cands if len(c) >= 2}


def _split_head(text: str) -> tuple[str | None, str | None]:
    """书眉拆分 → (页码数字, 标题)。'012 民法总论'→('012','民法总论')；
    '民法总论'→(None,'民法总论')；'012'→('012',None)。
    用途：header 文本归一（页码并入 page_number），避免每种带页码的页眉
    变体都被 popo 的"首次出现保留为 text"规则各留一条正文（病例 033 追记）。"""
    t = text.strip()
    if not t:
        return None, None
    m = re.match(r"^(\d{1,4})\s+(.+)$", t)
    if m:
        return m.group(1), m.group(2).strip()
    m = re.match(r"^(.+?)\s+(\d{1,4})$", t)
    if m:
        return m.group(2), m.group(1).strip()
    return (t, None) if t.isdigit() else (None, t)


_HEAD_NUM_LEFT = re.compile(r"^\d{1,4}\s+(.+?)\s*$")
_HEAD_NUM_RIGHT = re.compile(r"^(.+?)\s*\d{1,4}\s*$")


def _demote_running_heads(blocks: list[dict], cands: set,
                          title_freq: dict | None = None) -> list[dict]:
    """页首书眉降级：前两个 text/title 块里的书眉形态短块降为 header
    （噪声，不渲染、不截断跨页合并）。

    两路判别（FG ch12 与 QFT 书眉双实测，两头误伤都防）：
    - **text 块**：剥首/尾数字后匹配书眉候选（'012 民法总论' 形态）；
    - **title 块**：仅当该文本作为 title 块在全书出现 ≥3 次才降——真章题
      全书只出现一次（章题与书眉同文也不怕：FG '12  All-or-Nothing
      Thinking' 全文含章号，书眉字段无号，天然豁免）；书眉被模型标成
      title 时必然在多页复发（QFT '2.2 The Lorentz grou' 每节页页如此）。"""
    if not cands and not title_freq:
        return blocks
    out = []
    for idx, b in enumerate(blocks):
        if idx <= 1 and b.get("t") in ("text", "title"):
            t = str(b.get("text", "")).strip()
            if 0 < len(t) <= 42:
                if b.get("t") == "title":
                    if title_freq and title_freq.get(_norm_key(t), 0) >= 3:
                        out.append({"t": "header", "text": t})
                        continue
                else:
                    m = _HEAD_NUM_LEFT.match(t) or _HEAD_NUM_RIGHT.match(t)
                    if m and _norm_key(m.group(1)) in cands:
                        out.append({"t": "header", "text": t})
                        continue
        out.append(b)
    return out


_CIRCLED_RE = re.compile(r"([①-⑳])")


def _wrap_page_markers(text: str, fn_markers: set) -> str:
    """正文里的脚注圈码 → MinerU 上标约定 $^{①}$（stage3 的 noteref 锚定依赖
    该形态）。只包**本页确有对应脚注**的圈码——无对应注文的圈码保持裸字
    （多为正文内联列举），失败方向=不动作。"""
    if not text or not fn_markers:
        return text
    return _CIRCLED_RE.sub(
        lambda m: f"$^{{{m.group(1)}}}$" if m.group(1) in fn_markers else m.group(1),
        text)


_PAGE_SUFFIX_RE = re.compile(r"\s*[\.·…•\-—–\s]{2,}\s*\d{1,4}\s*$")


def _strip_page_suffix(text: str) -> str:
    """剥标题/目录条目尾部的点线页码（'7.1 The QED Lagrangian … 180' →
    '7.1 The QED Lagrangian'）。只用于标题/条目，正文不动。"""
    return _PAGE_SUFFIX_RE.sub("", text or "").strip()


_TOC_HEAD_RE = re.compile(r"目\s*录|简\s*目|详\s*目|contents?", re.I)


def _toc_region_pages(pages_json: dict[int, dict]) -> set[int]:
    """真目录区判定：含 ≥3 个 toc 块的连续页组（gap≤1）。
    定位优先级：含"目录/CONTENTS"页首词的组 > 前 50% 内最早的组；
    目录标题词组之后 gap≤3 的相邻组并入（简目+详目被扉页隔开的情形）；
    书后半部的稠密组（索引页等）一律排除（必须保卫社会实测：索引页
    331-332 比真目录 3-5 更稠密，"取最多"被偷走）。
    章首 mini-TOC（孤立、条目少）不是目录页——其条目按普通正文处理，
    防 stage2 目录区检测吞掉正文起点（QFT 实测：front_matter toc
    6-59 吞掉 ch1-3，病例 034）。"""
    dense = {p for p, pj in pages_json.items()
             if sum(1 for b in pj.get("blocks", []) if b.get("t") == "toc") >= 3}
    if not dense:
        return set()
    groups: list[list[int]] = [[]]
    for p in sorted(dense):
        if groups[-1] and p - groups[-1][-1] > 1:
            groups.append([])
        groups[-1].append(p)
    groups = [g for g in groups if g]
    if not groups:
        return set()

    def has_heading(p: int) -> bool:
        for b in pages_json[p].get("blocks", [])[:3]:
            if b.get("t") in ("title", "text") and _TOC_HEAD_RE.search(str(b.get("text", ""))[:30]):
                return True
        return False

    max_page = max(pages_json) if pages_json else 0
    head_groups = [g for g in groups if any(has_heading(p) for p in g)]
    if head_groups:
        region: set[int] = set()
        for gi, g in enumerate(groups):
            if g in head_groups:
                region |= set(g)
                # 吸收紧随的相邻组（gap≤3，简目/详目分离情形）
                for g2 in groups[gi + 1:]:
                    if g2[0] - g[-1] <= 3 and g2[0] <= max_page * 0.5:
                        region |= set(g2)
                        g = g2
                    else:
                        break
                break  # 目录只可能有一处
        return region
    # 无标题词：取前 50% 内最早稠密组（失败方向=宁缺毋滥，索引在后天然排除）
    for g in groups:
        if g[0] <= max_page * 0.5:
            return set(g)
    return set()


def _drop_minitoc_lines(pages_json: dict[int, dict], toc_pages: set,
                        toc_entries: list) -> int:
    """章首 mini-TOC 行丢弃守卫（病例 034 追记，用户亲读判定灾难级）。

    同时满足四个条件才丢弃（失败方向=不动作，缺一保留）：
    ① 不在真目录区；② 行文本归一化后命中目录先验条目；③ 同页连续 ≥2 条
    此类行（mini-TOC 成串形态；真标题块参与成串判定但**永不丢弃**，只丢
    toc 型行）；④ 该 toc 行同文本在更后位置（更后页，或本页更后块位——
    章首页 mini-TOC 与真节标题同页并存）还有 title/text 块（零信息损失）。
    """
    norms = {_norm_key(e.get("text", "")) for e in toc_entries if e.get("text")}
    if not norms:
        return 0
    later: dict[str, list] = {}
    for p, pj in pages_json.items():
        for bi, b in enumerate(pj.get("blocks", [])):
            if b.get("t") in ("title", "text"):
                k = _norm_key(_strip_page_suffix(b.get("text", "")))
                if k:
                    later.setdefault(k, []).append((p, bi))

    def _has_downstream(k: str, p: int, bi: int) -> bool:
        return any((pp, bb) > (p, bi) for pp, bb in later.get(k, []))

    dropped = 0
    for p, pj in pages_json.items():
        if p in toc_pages:
            continue
        blocks = pj.get("blocks", [])
        out: list[dict] = []
        i = 0
        while i < len(blocks):
            b = blocks[i]
            k = _norm_key(_strip_page_suffix(b.get("text", ""))) \
                if b.get("t") in ("toc", "title") else None
            if k and k in norms:
                # 收集连续命中串（title 参与成串判定）
                j = i
                run = []
                while j < len(blocks):
                    kj = _norm_key(_strip_page_suffix(blocks[j].get("text", ""))) \
                        if blocks[j].get("t") in ("toc", "title") else None
                    if kj and kj in norms:
                        run.append((blocks[j], kj, j))
                        j += 1
                    else:
                        break
                if len(run) >= 2:
                    for bb, kk, bj in run:
                        if bb.get("t") == "toc" and _has_downstream(kk, p, bj):
                            dropped += 1
                            continue      # 丢（仅 toc 型、有下游真标题）
                        out.append(bb)  # title 一律保留；无下游的 toc 保留
                    i = j
                    continue
            out.append(b)
            i += 1
        pj["blocks"] = out
    return dropped


def _merge_bare_number_titles(blocks: list[dict]) -> list[dict]:
    """裸编号标题与紧随的标题块合并：章题被模型拆成 '7' + 'Quantum
    electrodynamics' 两块时合并为 '7 Quantum electrodynamics'（QFT 病例：
    裸 '7' 单块成了幻影孤儿章）。仅 ^\\d{1,3}$ / ^第…[编章]$ 形态且紧邻
    下一块同为 title 才合并；否则原样保留（不动作）。"""
    out: list[dict] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.get("t") == "title" and re.fullmatch(r"(\d{1,3}|第[一二三四五六七八九十百零〇\d]+[编章]?)",
                                                  str(b.get("text", "")).strip()) \
                and i + 1 < len(blocks) and blocks[i + 1].get("t") == "title":
            nxt = blocks[i + 1]
            merged = {"t": "title",
                      "text": f"{str(b['text']).strip()} {str(nxt.get('text', '')).strip()}".strip(),
                      "level": nxt.get("level", b.get("level", 1))}
            out.append(merged)
            i += 2
            continue
        out.append(b)
        i += 1
    return out


_MD_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


_SEP_CELL_RE = re.compile(r":?-{2,}:?")
_SEP_ROW_TEXT_RE = re.compile(r"\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|")


def _unsquash_pipe_table(text: str) -> str:
    """单行压扁的管道表还原为多行形态：VLM 偶把整表挤成一行
    （'| A | B |---|---| | 1 | 2 |'，FG p309 实测）。以分隔行文本（带管道
    符整体匹配）为锚切头/体两区，避开行边界 '| |' 产生幽灵空格的歧义；
    尾部不足一行的残片（跨页截断）退为表后文本行，内容不丢。
    歧义/无法还原的行原样保留（不动作）。"""
    lines = (text or "").splitlines()
    if any(_MD_SEP_RE.match(ln.strip()) for ln in lines):
        return text  # 已是多行形态
    if "---" not in text or "|" not in text:
        return text

    def cells_of(region: str) -> list[str]:
        region = region.strip().strip("|")
        return [c.strip() for c in region.split("|")] if region.strip() else []

    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not (s.startswith("|") and "---" in s):
            out.append(ln)
            continue
        m = _SEP_ROW_TEXT_RE.search(s)
        if not m:
            out.append(ln)
            continue
        ncol = m.group(0).count("|") - 1
        head = cells_of(s[:m.start()])
        body = cells_of(s[m.end():])
        if not ncol or not head or len(head) % ncol or not body:
            out.append(ln)  # 歧义 → 原样保留
            continue
        full = len(body) - len(body) % ncol
        if full == 0:
            out.append(ln)
            continue
        out += ["| " + " | ".join(head[k:k + ncol]) + " |"
                for k in range(0, len(head), ncol)]
        out.append("|" + "|".join(["---"] * ncol) + "|")
        out += ["| " + " | ".join(body[k:k + ncol]) + " |"
                for k in range(0, full, ncol)]
        if body[full:]:  # 跨页截断残片 → 表后文本行
            out.append(" ".join(body[full:]))
    return "\n".join(out)


def _table_md_to_html(md: str) -> str | None:
    """markdown pipe 表格 → HTML table（构造性良构，单元格全转义）。

    宽容三点（FG 实测形态）：单行压扁表先 _unsquash 还原；分隔行允许落在
    前 3 行内（双行分组表头，Anger Scale 形态），分隔行之前每行都作 thead
    行；单列放行（(✓) 清单表/跨页列表碎片）。无分隔行/无 body 行仍返回
    None——调用方回退普通文本块，内容不丢（失败方向=不动作）。"""
    import html as _h
    lines = [ln.strip() for ln in _unsquash_pipe_table(md or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    sep_idx = next((k for k in range(min(3, len(lines)))
                    if _MD_SEP_RE.match(lines[k])), None)
    if not sep_idx:  # None 或 0（分隔行打头无表头）
        return None

    def cells(row: str) -> list[str]:
        row = row.strip().strip("|")
        return [c.strip() for c in row.split("|")]

    heads = [cells(r) for r in lines[:sep_idx]]
    body = [cells(r) for r in lines[sep_idx + 1:]]
    ncol = max(len(h) for h in heads)
    if not body:
        return None
    parts = ["<table><thead>"]
    for h in heads:
        h = (h + [""] * ncol)[:ncol]
        parts.append("<tr>" + "".join(f"<th>{_h.escape(c)}</th>" for c in h) + "</tr>")
    parts.append("</thead><tbody>")
    for r in body:
        r = (r + [""] * ncol)[:ncol]
        parts.append("<tr>" + "".join(f"<td>{_h.escape(c)}</td>" for c in r) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _split_embedded_table(text: str) -> list[dict] | None:
    """text 块内嵌管道表抢救（模型把表格标成 text）：找分隔行 → 其前连续
    管道行为表头、其后连续管道行为表体；前后残余文本各自成 text 块返回。
    切不出合法表返回 None——调用方原样发射，不动作。

    返回 [{"kind": "text"|"table", "text"|"html": ...}, ...]（保序）。"""
    lines = _unsquash_pipe_table(text or "").splitlines()
    sep_k = next((k for k, ln in enumerate(lines)
                  if _MD_SEP_RE.match(ln.strip())), None)
    if not sep_k:
        return None
    h = sep_k - 1
    while h - 1 >= 0 and lines[h - 1].strip().startswith("|"):
        h -= 1
    head_lines = [ln for ln in lines[h:sep_k] if ln.strip()]
    if not head_lines or not all(ln.strip().startswith("|") for ln in head_lines):
        return None
    body_lines: list[str] = []
    b = sep_k + 1
    while b < len(lines):
        s = lines[b].strip()
        if s.startswith("|"):
            body_lines.append(lines[b])
        elif s:
            break
        b += 1
    if not body_lines:
        return None
    tbl_html = _table_md_to_html("\n".join(head_lines + [lines[sep_k]] + body_lines))
    if not tbl_html:
        return None
    out: list[dict] = []
    before = "\n".join(lines[:h]).strip()
    after = "\n".join(lines[b:]).strip()
    if before:
        out.append({"kind": "text", "text": before})
    out.append({"kind": "table", "html": tbl_html})
    if after:
        out.append({"kind": "text", "text": after})
    return out


_DANGLE_SAME_RE = re.compile(r"\$\$\s*\\?qqu[ad]*\s*(\(\d+\.\d+\))")
_DANGLE_HEAD_RE = re.compile(r"^\s*(\(\d+\.\d+\))\s*(?=\S)")


def _fix_dangling_eq_numbers(blocks: list[dict]) -> list[dict]:
    """游离公式编号归位（模型把编号写到 $$ 块外，以 \\qqud(3.47) 裸源码
    或块首裸编号紧贴下文单词的形态泄漏）：
    A) 块内 '$$…$$ \\qqud (3.47)' → 编号并入 $$ 内（\\qquad 规范间距）；
    B) 块首裸编号 (3.47) 且上一 text 块以 $$ 结尾 → 编号移入上块 $$ 内。
    编号限定带点形式 (章.序号)——'(1) 第一点' 式列表标记不吃（不动作）。"""
    prev_text = None
    for b in blocks:
        if b.get("t") != "text":
            prev_text = None
            continue
        t = str(b.get("text", ""))
        if "$$" in t:
            t = _DANGLE_SAME_RE.sub(lambda m: f" \\qquad {m.group(1)}$$", t)
        m = _DANGLE_HEAD_RE.match(t)
        if m and prev_text is not None:
            pt = str(prev_text.get("text", "")).rstrip()
            if pt.endswith("$$"):
                prev_text["text"] = pt[:-2].rstrip() + f" \\qquad {m.group(1)}$$"
                t = t[m.end():]
        b["text"] = t
        if t.strip():
            prev_text = b
    return [b for b in blocks if b.get("t") != "text" or str(b.get("text", "")).strip()]


def _head_is_running(rh: str, printed_page, toc_entries: list) -> bool:
    """书眉==当前章节标题且已深入章节内部（printed_page > 条目印刷页）→
    判定为真书眉，不再发射 header 块（防 popo 首现规则复活进正文、再被
    锚点晋升成正文段落——'3.1 The action principle where L…' 连排病例）。
    章首页的书眉（printed_page ≤ 条目页，病例 004 的晋升通道）与
    页码缺失/条目无页码一律保留（不动作）。"""
    k = _norm_key(rh)
    if not k or not isinstance(printed_page, int):
        return False
    for e in toc_entries:
        ep = e.get("page")
        if isinstance(ep, int) and _norm_key(e.get("text", "")) == k:
            return printed_page > ep
    return False


def _toc_scheme_hint(entries: list[dict]) -> str:
    """从已落库目录条目归纳层级规则提示（'第X编'=1, '第X章'=2…）。取众数防个别错。"""
    votes: dict[str, dict[int, int]] = {}
    for e in entries:
        t, lv = e.get("text", ""), e.get("level")
        if not isinstance(lv, int):
            continue
        pat = ("第X编" if re.match(r"^第[一二三四五六七八九十百零〇\d]+编", t)
               else "第X章" if re.match(r"^第[一二三四五六七八九十百零〇\d]+章", t)
               else "第X节" if re.match(r"^第[一二三四五六七八九十百零〇\d]+节", t)
               else "一、二、…（汉字序号小节）" if re.match(r"^[一二三四五六七八九十]+、", t)
               else None)
        if pat:
            votes.setdefault(pat, {})[lv] = votes.setdefault(pat, {}).get(lv, 0) + 1
    return "，".join(f"{k}=L{max(v, key=v.get)}" for k, v in votes.items())


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class VlmProvider:
    """多模态逐页阅读引擎（OcrProvider 协议）。"""

    name = "vlm"

    def parse(self, pdf_path: str, work_dir: str, ocr: bool = True,
              progress=None, **opts) -> dict:
        import fitz  # PyMuPDF，延迟导入

        pdf_path = str(pdf_path)
        stem = Path(pdf_path).stem
        out_dir = Path(work_dir) / self.name
        out_dir.mkdir(parents=True, exist_ok=True)
        images_dir = out_dir / "images"
        _report = progress or (lambda *a, **kw: None)

        # ---- 配置（opts 可覆盖，供测试/调试）----
        text_client = VlmClient(
            base_url=opts.get("base_url", config.VLM_BASE_URL),
            api_key=opts.get("api_key", config.VLM_API_KEY),
            model=opts.get("model", config.VLM_MODEL),
            reasoning=opts.get("reasoning", config.VLM_REASONING),
        )
        # 图片定位与转写同源（同模型出粗框 + 光栅重裁），零额外 key
        bbox_client = None if opts.get("no_images") else text_client
        workers = int(opts.get("workers", config.VLM_WORKERS))
        dpi = int(opts.get("dpi", config.VLM_DPI))
        crop_dpi = int(opts.get("crop_dpi", 300))
        start_page = int(opts.get("start_page", 0))
        end_page = opts.get("end_page")  # 0 起，不含

        if not text_client.client.api_key:
            raise RuntimeError("VLM_API_KEY 未配置（见 .env.example）")

        doc = fitz.open(pdf_path)
        total = doc.page_count
        end_page = min(int(end_page), total) if end_page else total
        page_range = list(range(start_page, end_page))

        logger.info(f"Stage 1: VLM 逐页阅读 '{Path(pdf_path).name}'")
        logger.info(f"  {total} 页，本次处理 {len(page_range)} 页，模型 {text_client.model} "
                    f"(思考 {text_client.reasoning})，图片定位 "
                    f"{bbox_client.model if bbox_client else '关闭'}，workers={workers}")

        db = _StateDB(out_dir / "vlm_state.db")
        pages_json: dict[int, dict] = {}
        bodies: dict[int, str] = {}
        lock = threading.Lock()
        t_start = time.time()

        # 断点续跑：回填已完成页
        for p in page_range:
            row = db.get_page(p)
            if row and row[0] == "ok" and row[1]:
                pages_json[p] = json.loads(row[1])
                bodies[p] = "\n".join(b.get("text", "") for b in pages_json[p].get("blocks", [])
                                      if b.get("t") in ("text", "title"))
        if pages_json:
            logger.info(f"  断点续跑：{len(pages_json)} 页已完成，跳过")

        def read_page(p: int) -> None:
            if p in pages_json:
                return
            try:
                pix = doc[p].get_pixmap(dpi=dpi)
                img = pix.tobytes("png")
                with lock:
                    tail = bodies.get(p - 1, "")
                    scheme = _toc_scheme_hint(json.loads(db.get("toc_entries") or "[]"))
                prompt = _PAGE_PROMPT.format(
                    prev_tail_block=_PREV_TAIL_TMPL.format(tail=tail[-300:]) if tail else "",
                    toc_hint_block=_TOC_HINT_TMPL.format(scheme=scheme) if scheme else "")
                r = text_client.chat(prompt, images=[img], want_json=True)
                if not r["ok"]:
                    db.save_page(p, "error", None, 0, 0, False, r.get("error", ""), None)
                    logger.error(f"  第 {p} 页失败（不动作，留待重跑）: {r.get('error', '')[:120]}")
                    return
                pj = r["json"]
                # 形状栈/类型守卫：坏块降级为 text，绝不丢弃内容
                cleaned = []
                for b in pj.get("blocks") or []:
                    if not isinstance(b, dict):
                        continue
                    t = b.get("t")
                    if t == "image":
                        cleaned.append(b)   # 图块内容在 caption，无 text 字段
                        continue
                    if not str(b.get("text", "")).strip():
                        continue
                    if t == "title":
                        lv = b.get("level")
                        b["level"] = min(max(int(lv), 1), 6) if isinstance(lv, (int, float)) else 1
                        cleaned.append(b)
                    elif t in ("text", "image", "toc", "table"):
                        cleaned.append(b)
                    else:
                        cleaned.append({"t": "text", "text": str(b.get("text", ""))})
                fns = [f for f in (pj.get("footnotes") or [])
                       if isinstance(f, dict) and str(f.get("text", "")).strip()]
                pj["blocks"] = cleaned
                pj["footnotes"] = fns
                pj["has_image"] = any(b.get("t") == "image" for b in cleaned) or bool(pj.get("has_image"))

                # 目录页：条目落库（先验，简目/详目去重）
                if pj.get("kind") == "toc" or any(b.get("t") == "toc" for b in cleaned):
                    with lock:
                        toc_entries = json.loads(db.get("toc_entries") or "[]")
                        have = {_norm_key(e.get("text", "")) for e in toc_entries}
                        for b in cleaned:
                            if b.get("t") == "toc":
                                k = _norm_key(b.get("text", ""))
                                if k and k not in have:
                                    toc_entries.append({"text": b.get("text", "").strip(),
                                                        "level": b.get("level"),
                                                        "page": b.get("page")})
                                    have.add(k)
                        db.set("toc_entries", json.dumps(toc_entries, ensure_ascii=False))
                    logger.info(f"  第 {p} 页：目录，累计 {len(toc_entries)} 条先验")

                db.save_page(p, "ok", pj, len(cleaned), len(fns),
                             pj["has_image"], "", r["latency_s"])
                with lock:
                    pages_json[p] = pj
                    bodies[p] = "\n".join(b.get("text", "") for b in cleaned
                                          if b.get("t") in ("text", "title"))
                    _report(f"VLM 阅读 {len(pages_json)}/{len(page_range)} 页",
                            len(pages_json) / max(len(page_range), 1))
            except Exception as e:  # 渲染等非 API 异常：同样不动作
                db.save_page(p, "error", None, 0, 0, False, f"{type(e).__name__}: {e}", None)
                logger.error(f"  第 {p} 页异常（不动作）: {e}")

        try:
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    list(ex.map(read_page, page_range))
            else:
                for p in page_range:
                    read_page(p)
        finally:
            doc.close()

        # ---- 图片：bbox 定位 + 高分辨率裁剪（仅图片页，逐个顺序处理）----
        n_images = 0
        if bbox_client:
            for p in sorted(pages_json):
                pj = pages_json[p]
                if not pj.get("has_image") or not _needs_image_extract(pj, images_dir):
                    continue
                _report(f"图片定位 第 {p} 页", None)
                n_images += self._extract_images(pdf_path, p, pj,
                                                 bbox_client, images_dir, crop_dpi)
                # img_path/bbox/降级结果回写 db：续跑回填与 content_list 重建都
                # 以 db 为唯一事实源，只进内存会让 image 块在重建时全丢
                # （2026-09-17 五书重放事故，图块归零）
                db.save_page(p, "ok", pj, len(pj.get("blocks", [])),
                             len(pj.get("footnotes", [])),
                             bool(pj.get("has_image")), "", None)

        # ---- 汇总为 content_list 契约 ----
        toc_pages = _toc_region_pages(pages_json)
        toc_entries = json.loads(db.get("toc_entries") or "[]")
        n_dropped = _drop_minitoc_lines(pages_json, toc_pages, toc_entries)
        if n_dropped:
            logger.info(f"  章首 mini-TOC 丢弃 {n_dropped} 行（下游有真标题，零信息损失）")
        content_list = self._to_content_list(pages_json, toc_entries, toc_pages)
        md = self._to_markdown(pages_json)
        (out_dir / f"{stem}_content_list.json").write_text(
            json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")

        failed = db.failed_pages()
        meta = {
            "provider": self.name,
            "model": text_client.model, "reasoning": text_client.reasoning,
            "bbox_model": bbox_client.model if bbox_client else None,
            "dpi": dpi, "workers": workers,
            "pages_total": total, "pages_read": len(pages_json),
            "failed_pages": failed,
            "toc_entries": len(json.loads(db.get("toc_entries") or "[]")),
            "images": n_images,
            "content_blocks": len(content_list),
            "wall_s": round(time.time() - t_start, 1),
            "images_dir": str(images_dir),
        }
        (out_dir / "metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        db.close()

        if failed:
            logger.warning(f"  {len(failed)} 页失败（连续页覆盖 QC 会报红）: {failed[:20]}")
        logger.info(f"  VLM 完成: {len(content_list)} blocks, "
                    f"{meta['toc_entries']} 条目录先验, {n_images} 张图, "
                    f"耗时 {meta['wall_s']:.0f}s")
        return {"content_list": content_list, "images_dir": str(images_dir),
                "markdown": md}

    # ------------------------------------------------------------------
    # 图片提取：bbox 定位 + clip 裁剪。失败方向：image 块降级为文字占位，不丢内容。
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_boxes(client: VlmClient, png: bytes) -> list[list[float]]:
        """bbox 定位 + 宽容解析：{"images":[...]} / 裸数组 [...] / 单框 {"bbox":...}。"""
        r = client.chat(_IMAGE_BBOX_PROMPT, images=[png], max_tokens=4096, want_json=True)
        if not r["ok"] or not r["json"]:
            return []
        d = r["json"]
        items = d.get("images") if isinstance(d, dict) else (d if isinstance(d, list) else None)
        if items is None and isinstance(d, dict) and isinstance(d.get("bbox"), (list, tuple)):
            items = [d]
        boxes = []
        for x in items or []:
            b = x.get("bbox") if isinstance(x, dict) else None
            if isinstance(b, (list, tuple)) and len(b) == 4 \
                    and all(isinstance(v, (int, float)) for v in b):
                boxes.append([float(v) for v in b])
        return boxes

    def _extract_images(self, pdf_path: str, p: int, pj: dict,
                        bbox_client: VlmClient, images_dir: Path,
                        crop_dpi: int) -> int:
        import fitz

        img_blocks = [b for b in pj.get("blocks", []) if b.get("t") == "image"]
        try:
            with fitz.open(pdf_path) as doc:
                page = doc[p]
                low_png = page.get_pixmap(dpi=150).tobytes("png")
                boxes = self._detect_boxes(bbox_client, low_png)
                if not boxes:
                    raise ValueError("bbox 定位失败/为空")

                # 光栅重裁：多图重叠合并 → 连通域吸附（粗框为种子，纯规则，失败回退原框）
                from io import BytesIO
                from PIL import Image
                from raster_snap import merge_overlaps, snap_box_cc
                page_img = Image.open(BytesIO(low_png))
                boxes = [b for b in boxes
                         if all(0 <= v <= 1200 for v in b) and b[2] - b[0] > 20 and b[3] - b[1] > 20]
                if not boxes:
                    raise ValueError("bbox 全部退化")
                boxes = merge_overlaps(boxes)
                boxes = [snap_box_cc(page_img, b) for b in boxes]
                boxes = _reading_order(boxes)  # 行感知阅读序（同行多图防 y 噪声翻转）

                images_dir.mkdir(parents=True, exist_ok=True)
                rect = page.rect  # PDF points
                n_saved = 0
                for i, b in enumerate(boxes):
                    pad = 0.01
                    x1 = max(0.0, (b[0] / 1000 - pad)) * rect.width
                    y1 = max(0.0, (b[1] / 1000 - pad)) * rect.height
                    x2 = min(1.0, (b[2] / 1000 + pad)) * rect.width
                    y2 = min(1.0, (b[3] / 1000 + pad)) * rect.height
                    if x2 - x1 < 10 or y2 - y1 < 10:
                        continue
                    clip = fitz.Rect(x1, y1, x2, y2)
                    pix = page.get_pixmap(dpi=crop_dpi, clip=clip)
                    name = f"p{p:04d}_{i}.png"
                    (images_dir / name).write_bytes(pix.tobytes("png"))
                    # 与 caption 块按序配对
                    blk = img_blocks[i] if i < len(img_blocks) else None
                    if blk is not None:
                        blk["img_path"] = f"images/{name}"
                        blk["bbox"] = [round(v, 1) for v in b]
                    n_saved += 1
                # caption 块多于实际图：多余的降级为文字占位
                for b in img_blocks[n_saved:]:
                    b["t"] = "text"
                    b["text"] = f"[插图{('：' + b['caption']) if b.get('caption') else ''}]"
                return n_saved
        except Exception as e:
            logger.warning(f"  第 {p} 页图片提取失败，降级为文字占位（不丢内容）: {e}")
            for b in pj.get("blocks", []):
                if b.get("t") == "image":
                    b["t"] = "text"
                    b["text"] = f"[插图{('：' + b['caption']) if b.get('caption') else ''}]"
            pj["has_image"] = False
            return 0

    # ------------------------------------------------------------------
    # 契约转换
    # ------------------------------------------------------------------

    def _to_content_list(self, pages_json: dict[int, dict],
                         toc_entries: list | None = None,
                         toc_pages: set | None = None) -> list[dict]:
        toc_pages = toc_pages if toc_pages is not None else _toc_region_pages(pages_json)
        head_cands = _head_candidates(pages_json, toc_entries or [])
        # 全书 title 块文本频次（书眉被标成 title 时必复发，真章题全书一次）
        from collections import Counter as _Counter
        title_freq = _Counter(
            _norm_key(str(b.get("text", "")))
            for pj in pages_json.values() for b in pj.get("blocks", [])
            if b.get("t") == "title" and str(b.get("text", "")).strip())
        out = []
        for p in sorted(pages_json):
            pj = pages_json[p]
            blocks = _fix_dangling_eq_numbers(_demote_running_heads(
                _merge_bare_number_titles(pj.get("blocks", [])),
                head_cands, title_freq))
            fns = pj.get("footnotes", [])
            fn_markers = {str(f.get("marker", "")).strip() for f in fns
                          if str(f.get("marker", "")).strip()}
            n_slots = len(blocks) + len(fns) + 2
            i = 0
            if pj.get("running_head"):
                num, title = _split_head(str(pj["running_head"]))
                if num and pj.get("printed_page") is None and num.isdigit():
                    pj["printed_page"] = int(num)
                if title and not _head_is_running(title, pj.get("printed_page"),
                                                  toc_entries or []):
                    out.append({"type": "header", "text": title,
                                "bbox": [110, 20, 890, 55], "page_idx": p})
            if pj.get("printed_page") is not None:
                out.append({"type": "page_number", "text": str(pj["printed_page"]),
                            "bbox": [430, 955, 570, 985], "page_idx": p})
            for b in blocks:
                bbox = b.get("bbox") if isinstance(b.get("bbox"), list) else _synth_bbox(i, n_slots)
                t = b.get("t")
                if t == "header":
                    num, title = _split_head(b["text"])
                    if num:
                        out.append({"type": "page_number", "text": num,
                                    "bbox": [430, 955, 570, 985], "page_idx": p})
                    if title:
                        out.append({"type": "header", "text": title,
                                    "bbox": [110, 20, 890, 55], "page_idx": p})
                elif t == "title":
                    out.append({"type": "text",
                                "text": _wrap_page_markers(_strip_page_suffix(b["text"]), fn_markers),
                                "text_level": b.get("level", 1), "bbox": bbox, "page_idx": p})
                elif t == "toc":
                    if p in toc_pages:
                        # 真目录区：MinerU 形态（条目尾页码），供 stage2 目录提取
                        page = b.get("page")
                        txt = b["text"].strip() + (f" …… {page}" if page is not None else "")
                        out.append({"type": "text", "text": txt, "text_level": 1,
                                    "bbox": bbox, "page_idx": p})
                    else:
                        # 章首 mini-TOC / 误判：按普通正文（剥点线页码），
                        # 防目录区检测吞正文起点（病例 034）
                        txt = _wrap_page_markers(_strip_page_suffix(b["text"]), fn_markers)
                        if txt:
                            out.append({"type": "text", "text": txt,
                                        "bbox": bbox, "page_idx": p})
                elif t == "image" and b.get("img_path"):
                    cap = (b.get("caption") or "").strip()
                    out.append({"type": "image", "img_path": b["img_path"],
                                "image_caption": [cap] if cap else [],
                                "image_footnote": [],
                                "bbox": bbox, "page_idx": p})
                elif t == "table":
                    tbl_html = _table_md_to_html(str(b.get("text", "")))
                    cap = (b.get("caption") or "").strip()
                    if tbl_html:
                        if cap:
                            out.append({"type": "text", "text": cap,
                                        "bbox": bbox, "page_idx": p})
                        out.append({"type": "table", "table_body": tbl_html,
                                    "bbox": bbox, "page_idx": p})
                    else:
                        # pipe 解析失败 → 回退普通文本块，内容不丢；全管道行
                        # 形态剥首尾管道符（编号列表伪表格/跨页碎片，FG 认知
                        # 扭曲表实测），不往正文塞裸 markdown
                        raw = str(b.get("text", "")).strip()
                        rlines = [ln for ln in raw.splitlines() if ln.strip()]
                        if rlines and all(ln.strip().startswith("|") for ln in rlines):
                            raw = "\n".join(ln.strip().strip("|").strip() for ln in rlines)
                        out.append({"type": "text",
                                    "text": raw,
                                    "bbox": bbox, "page_idx": p})
                else:  # text 与降级占位
                    txt = b.get("text", "").strip()
                    if not txt and t == "image":
                        # image 块未配对到 img_path（无 bbox 客户端/定位失败）→ 文字占位，内容永不丢
                        txt = f"[插图{('：' + b['caption']) if b.get('caption') else ''}]"
                    if not txt:
                        continue
                    # text 块内嵌管道表抢救（模型标错类型，FG 实测 2 处）：
                    # 含分隔形态才尝试，切不出合法表原样发射（不动作）
                    if "|" in txt and "---" in txt:
                        txt = _unsquash_pipe_table(txt)
                        rescued = _split_embedded_table(txt)
                        if rescued:
                            for part in rescued:
                                if part["kind"] == "table":
                                    out.append({"type": "table",
                                                "table_body": part["html"],
                                                "bbox": bbox, "page_idx": p})
                                else:
                                    out.append({"type": "text",
                                                "text": _wrap_page_markers(part["text"], fn_markers),
                                                "bbox": bbox, "page_idx": p})
                            i += 1
                            continue
                    out.append({"type": "text",
                                "text": _wrap_page_markers(txt, fn_markers),
                                "bbox": bbox, "page_idx": p})
                i += 1
            for f in fns:
                marker = str(f.get("marker", "")).strip()
                out.append({"type": "page_footnote",
                            "text": f"{marker} {f['text'].strip()}".strip(),
                            "bbox": _synth_bbox(n_slots - 1, n_slots), "page_idx": p})
        return out

    def _to_markdown(self, pages_json: dict[int, dict]) -> str:
        parts = []
        for p in sorted(pages_json):
            pj = pages_json[p]
            parts.append(f"\n\n<!-- page {p} -->\n")
            for b in pj.get("blocks", []):
                t = b.get("t")
                if t == "title":
                    parts.append(f"\n{'#' * min(b.get('level', 1), 6)} {b['text'].strip()}\n")
                elif t == "toc":
                    page = b.get("page")
                    parts.append(f"- {b['text'].strip()}{f' … {page}' if page is not None else ''}")
                elif t == "image" and b.get("img_path"):
                    parts.append(f"\n![{b.get('caption') or ''}]({b['img_path']})\n")
                else:
                    parts.append(b.get("text", "").strip())
            for f in pj.get("footnotes", []):
                parts.append(f"\n> {str(f.get('marker', '')).strip()} {f['text'].strip()}")
        return "\n".join(parts)
