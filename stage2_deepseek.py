"""
Stage 2: DeepSeek V4 Flash — TOC 引导的语义结构分析

策略：
1. 先解析目录页(TOC)获取完整层级结构作为"路线图"
2. 然后在正文中定位每个章节，用 TOC 页码验证边界
3. 小标题（一、二、和（一）（二））根据上下文判断层级
"""

import json
import logging
import re
from pathlib import Path

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


def analyze_structure(markdown: str, content_list: list, book_name: str = "") -> dict:
    """用 DeepSeek 分析书籍结构——TOC 先行策略"""
    logger.info("Stage 2: DeepSeek V4 Flash TOC 引导结构分析")

    book_text = _build_page_marked_text(markdown, content_list)
    text_len = len(book_text)
    logger.info(f"  全文: {text_len:,} 字符 (~{text_len // 2:,} tokens)")

    if text_len > 900_000:
        logger.warning(f"  文本过长，截断到 900K 字符")
        book_text = book_text[:900_000]

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

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
        f"  DeepSeek: {len(raw_output):,} 字符, "
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
        f"  Stage 2 完成: {body_count} 章节 (TOC {toc_count} 项), "
        f"{fm_count} 前页, {bm_count} 后页"
    )

    return structure


def save_structure(structure: dict, output_dir: str) -> Path:
    path = Path(output_dir) / "structure.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)
    logger.info(f"  结构已保存: {path}")
    return path
