"""
Stage 2: DeepSeek V4 Flash — TOC 引导的语义结构分析

策略：
1. Pass 1: 解析目录页(TOC)获取高层结构（编/章/节）作为"路线图"
2. Pass 1.5: 从 TOC 结构提取层级映射表（锚定）
3. Pass 2: 收集所有候选标题，分批精修，产出完整大纲（complete_outline）
"""

import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位资深中文图书编辑，专精于图书结构分析和OCR后处理。

你的任务：阅读一本中文书籍的OCR全文（已按页标记），以目录页为锚点，分析全书结构。

## ⚠️ 关键原则：不要信任OCR工具的字号猜测

OCR工具（MinerU）会根据字号大小猜测标题级别（text_level），但这完全不可靠：
- 同一本书里，"第一章"有时被识别为level 1，有时被识别为level 2
- 字号大小不等于层级高低（有些正文字号比小标题还大）
- **你必须完全基于语义分析来确定层级，绝对不能依赖OCR工具的text_level**

## 分析步骤

### 第一步：找到目录页
- 在全文中搜索"目录"、"详目"、"Contents"等标记
- 目录页通常位于正文之前，包含章节标题和对应页码
- 目录是全书结构的权威来源

### 第二步：从目录推断层级结构
**仔细阅读目录的排版格式**，从中推断层级关系：
- 观察缩进、编号模式、字体大小差异
- 常见的层级标记（但不同书可能用不同术语！）：
  * 卷/篇/编/部分 → 最高层
  * 章 → 第二层
  * 节 → 第三层
  * 一、二、三、→ 第四层
  * （一）（二）（三）→ 第五层
  * 1. 2. 3. / (1) (2) (3) → 更细分
- **重要**：不同书的术语可能完全不同！
  * 有的书用"卷"而不是"编"
  * 有的书用"Part/Chapter"而不是"编/章"
  * 有的书根本没有最高层，直接从"章"开始
  * 你必须根据这本书的具体情况动态判断

### 第三步：在正文中定位章节
- 根据目录中的标题，在正文中找到对应的实际位置（[P{N}]标记）
- 每个章节的结束页 = 下一个同级或上级章节的起始页 - 1
- 目录标注的印刷页码可能有偏移，以正文中的[P{N}]标记为准

### 第四步：处理目录之外的细目标题
对于正文中存在但目录中没有列出的标题（如"一、"、"（一）"、"1."等）：
- 阅读上下文，判断它在层级中的位置
- 它属于哪个节？哪个章？
- **注意区分**：真正的层级标题 vs. 列举序号/思考题/案例编号
  * 真正的标题：后面紧跟具体内容阐述，通常独占一行
  * 列举序号/思考题：如"(1)乙对甲享有何种权利？"是题目，不是层级标题

### 第五步：分类前页和后页
- 目录页之前的内容是前页（封面、版权、献词、序言等）
- 正文结束后、索引/附录/后记等是后页

## 内容类型

### 前页 (front_matter)
- cover: 封面
- copyright: 版权页
- dedication: 献词
- toc: 目录页
- preface: 序言/前言

### 正文 (body)
- 每一条都是 chapter 类型
- 必须包含 title、level、page_start、page_end
- **level 必须基于你对目录的语义分析，不能照搬OCR的text_level**

### 后页 (back_matter)
- appendix: 附录
- bibliography: 参考文献
- index: 索引
- afterword: 后记

### 噪音 (noise)
- page_header: 页眉
- page_footer: 页脚
- scan_noise: 扫描噪点
- blank_page: 空白页

## 关键规则
1. **目录是权威来源**：章节标题必须与目录页一致（OCR可能有少量错字，选择最合理的版本）
2. 页码标记 [P{N}] 从1开始，N对应扫描件的实际页码
3. 目录页中列出的印刷页码仅供参考（可能有偏移），以 [P{N}] 标记为准
4. level 4 和 level 5 的小标题通常不在目录中列出，需要你根据正文上下文的语义关系判断
5. 对于"一、二、"开头的段落，判断它是否为真正的层级标题（通常后面紧跟具体内容阐述），还是单纯的列举序号
6. 不要跳过正文的任何章节，确保 TOC 中的每个条目都有对应的 body 条目
7. **再次强调：level 值必须来自你对目录结构的语义分析，绝对不能依赖OCR工具给出的text_level**
"""

USER_PROMPT_TEMPLATE = """以下是 OCR 识别后的书籍全文。文本中 [P{N}] 标记表示第 N 页的起始位置。

⚠️ 重要提示：文本中可能带有 # 标记（如 # 第一章、## 第一节），这些是OCR工具根据字号猜测的标题级别，**完全不可靠**，请不要参考这些 # 标记来判断层级。你必须通过阅读目录页来独立判断层级结构。

请按照分析步骤，输出如下JSON结构：

```json
{{
  "metadata": {{
    "title": "书名",
    "authors": ["作者"],
    "translator": "译者（无则null）",
    "publisher": "出版社",
    "language": "zh"
  }},
  "toc_structure": [
    {{"title": "目录条目原文", "level": 1-5, "toc_page_number": "目录标注的印刷页码"}}
  ],
  "front_matter": [
    {{"type": "cover|copyright|dedication|toc|preface|foreword", "label": "描述", "page_start": N, "page_end": N, "keep": true}}
  ],
  "body": [
    {{"type": "chapter", "title": "章节标题原文", "level": 1-5, "page_start": N, "page_end": N}}
  ],
  "back_matter": [
    {{"type": "appendix|bibliography|index|afterword", "label": "描述", "page_start": N, "page_end": N}}
  ],
  "noise_ranges": [
    {{"type": "page_header|page_footer|scan_noise|blank_page", "description": "描述", "pages": [N, ...]}}
  ],
  "toc_page_offset": N
}}
```

`toc_page_offset`: 如果目录标注的页码与 [P{{N}}] 之间有固定偏移（如目录写"第1页"对应[P15]），填偏移量（=15-1=14）。无反则填0。

注意：
- body 中的 chapter 必须覆盖 TOC 中的所有条目，一一对应
- **level 值必须基于你对目录结构的语义分析**（观察目录的缩进、编号模式），不要参考文本中的 # 标记
- level 4 的小标题作为独立 chapter 条目输出，有独立的 page_start/page_end（即到下一个同级或上级标题为止）
- 不要把思考题、案例分析题、练习题误判为层级标题（如"(1)乙对甲享有何种权利?"不是标题）
- 所有数字字段用整数，不要用字符串

=== 以下是书籍全文 ===

{book_text}"""


def _build_page_marked_text(markdown: str, content_list: list) -> str:
    """构建带页码标记的全文。每页起始位置插入 [P{N}] 标记。

    注意：不添加 # 等标题标记——MinerU 的 text_level 不可靠，
    DeepSeek 应从文本内容和目录结构自行判断层级。
    """
    if not content_list:
        return markdown

    pages = {}
    for block in content_list:
        page_idx = block.get("page_idx", 0)
        pages.setdefault(page_idx, []).append(block)

    lines = []
    for page_num in sorted(pages.keys()):
        lines.append(f"\n[P{page_num + 1}]\n")
        for block in pages[page_num]:
            btype = block.get("type", "")
            text = ""

            if btype in ("text", "title"):
                text = block.get("text", "")
                # 不添加 # 标记，让 DeepSeek 自行判断
            elif btype == "paragraph":
                text = block.get("text", "")
            elif btype == "image":
                caps = block.get("image_caption", [])
                cap = caps[0] if caps else ""
                text = f"[图片: {cap}]"
            elif btype == "table":
                text = f"[表格]\n{block.get('table_body', '')}\n[/表格]"
            elif btype == "list":
                items = block.get("list_items", []) or []
                text = "\n".join(f"- {item}" for item in items)
            elif btype in ("header", "footer", "page_number", "aside_text", "page_footnote"):
                text = block.get("text", "")
            else:
                text = block.get("text", "")

            if text.strip():
                lines.append(text)

    return "\n".join(lines)


def _clean_json_response(raw: str) -> str:
    """从 DeepSeek 响应中提取 JSON"""
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError(f"无法从响应中提取JSON: {raw[:500]}...")


def _validate_structure(structure: dict) -> dict:
    """验证并补全结构"""
    for key in ("metadata", "front_matter", "body", "back_matter", "noise_ranges"):
        structure.setdefault(key, [] if key != "metadata" else {})

    # 修复可能的字符串数字
    for ch in structure.get("body", []):
        for f in ("page_start", "page_end", "level"):
            if f in ch and isinstance(ch[f], str):
                try:
                    ch[f] = int(ch[f])
                except ValueError:
                    ch[f] = 0

    for fm in structure.get("front_matter", []):
        for f in ("page_start", "page_end"):
            if f in fm and isinstance(fm[f], str):
                try:
                    fm[f] = int(fm[f])
                except ValueError:
                    fm[f] = 0

    return structure


def _extract_level_mapping(toc_structure: List[Dict]) -> Dict[int, str]:
    """从 TOC 结构提取层级映射表（Pass 1.5：层级锚定）

    分析 toc_structure 中的标题模式，建立 level → 标题特征 的映射。
    例如：
        level 1 → "编" (第一编、第二编...)
        level 2 → "章" (第一章、第二章...)
        level 3 → "节" (第一节、第二节...)
        level 4 → "一、二、三" (小节，目录中未列出)
        level 5 → "（一）（二）" (细目，目录中未列出)

    返回: {level: description}
    """
    level_patterns = {}

    # 分析 toc_structure 中每个 level 的标题特征
    for item in toc_structure:
        level = item.get("level", 0)
        title = item.get("title", "")

        if level not in level_patterns:
            level_patterns[level] = []
        level_patterns[level].append(title)

    # 识别每个 level 的常见模式
    level_mapping = {}
    for level, titles in sorted(level_patterns.items()):
        # 检查常见模式
        if any("编" in t or "篇" in t or "卷" in t or "部" in t for t in titles):
            level_mapping[level] = "编/篇/卷/部 (最高层)"
        elif any("章" in t or re.match(r"^Chapter\s", t, re.I) for t in titles):
            level_mapping[level] = "章"
        elif any("节" in t or re.match(r"^§", t) for t in titles):
            level_mapping[level] = "节"
        elif any(re.match(r"^[一二三四五六七八九十]+、", t) for t in titles):
            level_mapping[level] = "一、二、三 (小节)"
        elif any(re.match(r"^（[一二三四五六七八九十]+）", t) for t in titles):
            level_mapping[level] = "（一）（二）(细目)"
        else:
            # 通用描述
            level_mapping[level] = f"层级 {level} 标题"

    # 推断 level 4 和 5 的默认映射（如果 TOC 中没有）
    max_level = max(level_mapping.keys()) if level_mapping else 3
    if 4 not in level_mapping and max_level < 4:
        level_mapping[4] = "一、二、三 (小节，目录中未列出)"
    if 5 not in level_mapping and max_level < 5:
        level_mapping[5] = "（一）（二）或 (1)(2) (细目，目录中未列出)"

    return level_mapping


def _collect_heading_candidates(content_list: List[Dict]) -> List[Dict]:
    """收集所有可能的标题候选（用于 Pass 2 精修）

    收集条件：
    - type = "title" 的 block
    - type = "text" 且 text_level >= 1 且文本 < 100 字符
    - 匹配常见子标题模式的 block（一、（一）等）

    返回: [{"page": N, "text": "...", "mineru_level": N, "context_before": "...", "context_after": "..."}]
    """
    candidates = []
    seen_texts = set()

    # 常见子标题模式
    sub_heading_patterns = [
        re.compile(r"^[一二三四五六七八九十]+、"),  # 一、二、三
        re.compile(r"^（[一二三四五六七八九十]+）"),  # （一）（二）
        re.compile(r"^\(\d+\)"),  # (1) (2)
        re.compile(r"^（\d+）"),  # （1）（2）
        re.compile(r"^\d+\.\s"),  # 1. 2.
    ]

    for i, block in enumerate(content_list):
        btype = block.get("type", "")
        text = block.get("text", "").strip()
        page = block.get("page_idx", 0) + 1

        if not text:
            continue

        is_candidate = False
        mineru_level = block.get("text_level", 0)

        # 条件 1: type = "title"
        if btype == "title":
            is_candidate = True
        # 条件 2: type = "text" 且 text_level >= 1 且 < 100 字符
        elif btype == "text" and mineru_level >= 1 and len(text) < 100:
            is_candidate = True
        # 条件 3: 匹配常见子标题模式
        elif any(p.match(text) for p in sub_heading_patterns):
            is_candidate = True

        if is_candidate:
            # 去重（相同文本只收集一次）
            if text in seen_texts:
                continue
            seen_texts.add(text)

            # 获取上下文（前后各 2 个 block）
            context_before = []
            for j in range(max(0, i - 2), i):
                t = content_list[j].get("text", "").strip()
                if t:
                    context_before.append(t[:150])  # 截断避免过长

            context_after = []
            for j in range(i + 1, min(len(content_list), i + 3)):
                t = content_list[j].get("text", "").strip()
                if t:
                    context_after.append(t[:150])

            candidates.append({
                "page": page,
                "text": text,
                "mineru_level": mineru_level,
                "context_before": "\n".join(context_before),
                "context_after": "\n".join(context_after),
            })

    return candidates


def _refine_headings(
    candidates: List[Dict],
    level_mapping: Dict[int, str],
    content_list: List[Dict],
    client: OpenAI,
    progress=None,
) -> List[Dict]:
    """分批精修候选标题（Pass 2）

    每批 100 个候选，发给 DeepSeek 判断：
    - "heading" + 层级（1-5）：真正的结构标题
    - "not_heading"：正文内容（问题、定义、案例、编号列举等）

    返回: complete_outline 列表
        [{"text": "...", "level": N, "page": N}, ...] 或
        [{"text": "...", "status": "not_heading", "reason": "..."}]
    """
    if not candidates:
        return []

    logger.info(f"  Pass 2: 精修 {len(candidates)} 个候选标题")

    # 构建层级映射描述
    level_desc = "\n".join(f"  - level {k}: {v}" for k, v in sorted(level_mapping.items()))

    system_prompt = f"""你正在分析一本书的目录结构。OCR 工具识别出了一些可能的标题，但需要你判断每一条是真正的结构标题还是正文内容。

## 本书的层级映射（严格遵守）

{level_desc}

## 判断规则

1. **真正的层级标题**（标记为 "heading"）：
   - 后面紧跟具体内容阐述（如章节标题后紧跟该章节的正文）
   - 通常独占一行或几行
   - 符合上述层级映射中的某种模式

2. **不是标题**（标记为 "not_heading"）：
   - 问题/思考题：如 "(1) 甲对乙享有何种权利?"
   - 定义/概念：如 "权利能力：自然人享有权利和承担义务的资格"
   - 案例编号：如 "案例 3.1"
   - 列举序号（非层级）：如 "(1) 第一项，(2) 第二项" 作为列举而非子标题
   - 正文中的短句：如 "因此，我们可以得出结论"

## 输出格式

JSON 数组，每条包含：
- "text": 原始文本
- "status": "heading" 或 "not_heading"
- 如果 status = "heading"，添加 "level": 1-5（严格符合层级映射）
- 如果 status = "not_heading"，添加 "reason": 简短原因

示例：
```json
[
  {{"text": "一、公、私法的划分", "status": "heading", "level": 4}},
  {{"text": "(1) 甲对乙享有何种权利?", "status": "not_heading", "reason": "问题"}},
  {{"text": "（一）权利的概念", "status": "heading", "level": 5}}
]
```"""

    outline = []
    batch_size = 100
    _report = progress or (lambda _: None)

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(candidates) + batch_size - 1) // batch_size

        _report(f"Pass 2: 批次 {batch_num}/{total_batches} ({len(batch)} 个候选)")
        logger.info(f"    批次 {batch_num}/{total_batches}: {len(batch)} 个候选")

        # 构建用户提示
        user_prompt = "以下是本批候选标题及其上下文：\n\n"
        for j, cand in enumerate(batch, 1):
            user_prompt += f"{j}. [P{cand['page']}] {cand['text']}\n"
            if cand["context_before"]:
                user_prompt += f"   前文: {cand['context_before'][:100]}...\n"
            if cand["context_after"]:
                user_prompt += f"   后文: {cand['context_after'][:100]}...\n"
            user_prompt += "\n"

        user_prompt += "\n请输出 JSON 数组。"

        # 调用 DeepSeek
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=8192,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
        )

        raw_output = resp.choices[0].message.content

        # 解析 JSON
        try:
            json_str = _clean_json_response(raw_output)
            batch_result = json.loads(json_str)

            if not isinstance(batch_result, list):
                logger.warning(f"    批次 {batch_num} 返回非数组，跳过")
                continue

            # 验证并添加到 outline
            for item in batch_result:
                text = item.get("text", "")
                status = item.get("status", "")

                if status == "heading":
                    level = item.get("level", 4)  # 默认 level 4
                    # 找到对应的 page
                    page = 0
                    for cand in batch:
                        if cand["text"] == text:
                            page = cand["page"]
                            break
                    outline.append({"text": text, "level": level, "page": page})
                elif status == "not_heading":
                    outline.append({"text": text, "status": "not_heading"})

            logger.info(f"      识别 {sum(1 for x in batch_result if x.get('status') == 'heading')} 个标题")

        except Exception as e:
            logger.warning(f"    批次 {batch_num} 解析失败: {e}")
            continue

    return outline


def analyze_structure(markdown: str, content_list: list, book_name: str = "",
                     progress=None) -> dict:
    """用 DeepSeek 分析书籍结构——三阶段策略

    Pass 1: 读全文，输出高层结构（编/章/节）
    Pass 1.5: 从 TOC 结构提取层级映射表（锚定）
    Pass 2: 收集候选标题，分批精修，产出完整大纲（complete_outline）

    Args:
        progress: 可选回调函数，接收字符串描述当前进度
    """
    logger.info("Stage 2: DeepSeek V4 Flash TOC 引导结构分析")
    _report = progress or (lambda _: None)

    book_text = _build_page_marked_text(markdown, content_list)
    text_len = len(book_text)
    logger.info(f"  全文: {text_len:,} 字符 (~{text_len // 2:,} tokens)")

    if text_len > 900_000:
        logger.warning(f"  文本过长，截断到 900K 字符")
        book_text = book_text[:900_000]

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    # ── Pass 1: 高层结构分析 ──
    _report("Pass 1: 正在分析高层结构 (编/章/节)...")
    logger.info("  Pass 1: 高层结构分析")
    user_prompt = USER_PROMPT_TEMPLATE.replace("{book_text}", book_text)

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=24576,
        temperature=0.1,
        extra_body={"thinking": {"type": "disabled"}},
    )

    raw_output = resp.choices[0].message.content
    usage = resp.usage
    logger.info(
        f"    {len(raw_output):,} 字符, "
        f"{usage.prompt_tokens:,} in + {usage.completion_tokens:,} out"
    )

    json_str = _clean_json_response(raw_output)
    structure = json.loads(json_str)
    structure = _validate_structure(structure)

    body_count = len(structure.get("body", []))
    toc_count = len(structure.get("toc_structure", []))
    fm_count = len(structure.get("front_matter", []))
    bm_count = len(structure.get("back_matter", []))

    logger.info(
        f"    完成: {body_count} 章节 (TOC {toc_count} 项), "
        f"{fm_count} 前页, {bm_count} 后页"
    )

    # ── Pass 1.5: 层级锚定 ──
    _report("Pass 1.5: 从目录提取层级映射...")
    toc_structure = structure.get("toc_structure", [])
    level_mapping = _extract_level_mapping(toc_structure)
    logger.info(f"  Pass 1.5: 层级映射 → {level_mapping}")

    # ── Pass 2: 标题精修 ──
    candidates = _collect_heading_candidates(content_list)
    _report(f"Pass 2: 收集到 {len(candidates)} 个候选标题，开始分批精修...")
    logger.info(f"  Pass 2: 收集到 {len(candidates)} 个候选标题")

    complete_outline = _refine_headings(candidates, level_mapping, content_list, client,
                                        progress=progress)

    # 将 complete_outline 添加到 structure
    structure["complete_outline"] = complete_outline

    heading_count = sum(1 for x in complete_outline if x.get("status") == "heading" or "level" in x)
    not_heading_count = sum(1 for x in complete_outline if x.get("status") == "not_heading")

    logger.info(
        f"    完成: {heading_count} 个标题, {not_heading_count} 个非标题"
    )

    return structure


def save_structure(structure: dict, output_dir: str) -> Path:
    path = Path(output_dir) / "structure.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)
    logger.info(f"  结构已保存: {path}")
    return path
