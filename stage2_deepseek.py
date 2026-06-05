"""
Stage 2: DeepSeek V4 Flash — 语义结构分析

输入 MinerU 输出的 Markdown，用 DeepSeek 通读全文后识别：
- 图书元数据（书名、作者、译者、出版社）
- 前页内容（封面、版权、献词、目录、序言）
- 正文章节（标题、层级、页码范围）
- 后页内容（附录、参考文献、索引、后记）
- OCR 噪音区域
"""

import json
import logging
import re
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

# ── DeepSeek System Prompt ─────────────────────────────────────
SYSTEM_PROMPT = """你是一位资深中文图书编辑，专精于图书结构分析和OCR后处理。

你的任务：阅读一本中文书籍的OCR全文（已按页标记），分析其语义结构，输出严格的JSON。

## 需要识别的内容类型

### 前页 (front_matter)
- cover: 封面信息（书名、作者大字、出版社logo）
- copyright: 版权页（ISBN、版次、图书在版编目CIP数据）
- dedication: 献词/题词
- toc: 目录页（章节列表+页码）
- preface: 序言/前言（他人作序或自序）
- foreword: 出版说明/凡例/编辑说明

### 正文 (body)
- chapter: 章节正文，需标注标题和层级
  - level 1: 篇/部分（如"第一部分"、"上篇"）
  - level 2: 章（如"第一章 xxx"）
  - level 3: 节（如"第一节 xxx"、"1. xxx"）
  注意：目录页中列的章节标题不算正文，不要重复标记

### 后页 (back_matter)
- appendix: 附录
- bibliography: 参考文献
- index: 索引
- afterword: 后记/跋
- colophon: 出版信息/版权说明

### 噪音 (noise)
- page_header: 页眉（每页顶部重复出现的书名/章节名）
- page_footer: 页脚（页码文字，如"·123·"）
- scan_noise: 扫描噪点文字
- blank_page: 空白页

## 重要规则
1. 章节标题必须使用原文中出现的精确文本，不要改写
2. page_start/page_end 使用文本中的 [P数字] 页码标记
3. 目录页(TOC)虽然属于前页，但其中列出的章节信息可作为验证章节结构的参考
4. 序言/前言也属于前页，不是正文第一章
5. 如果文本中有"目录"页，标记为 toc，其后的正文才是真正的章节开始
6. level 2 的章节通常有"第X章"格式，但也可能是"Chapter X"或纯数字编号
"""

USER_PROMPT_TEMPLATE = """以下是 OCR 识别后的书籍全文。文本中 [P{N}] 标记表示第 N 页的起始位置。

请仔细阅读全文，然后输出如下JSON结构：

```json
{
  "metadata": {
    "title": "书名（原文精确文字）",
    "authors": ["作者"],
    "translator": "译者（没有则填null）",
    "publisher": "出版社（没有则填null）",
    "language": "zh"
  },
  "front_matter": [
    {
      "type": "cover|copyright|dedication|toc|preface|foreword",
      "label": "人类可读的描述",
      "page_start": N,
      "page_end": N,
      "keep": true
    }
  ],
  "body": [
    {
      "type": "chapter",
      "title": "章节标题原文",
      "level": 1或2或3,
      "page_start": N,
      "page_end": N
    }
  ],
  "back_matter": [
    {
      "type": "appendix|bibliography|index|afterword",
      "label": "人类可读的描述",
      "page_start": N,
      "page_end": N
    }
  ],
  "noise_ranges": [
    {"type": "page_header|page_footer|scan_noise", "description": "描述", "pages": [N, N, ...]}
  ]
}
```

注意：
- 用 page_start/page_end 标注页码范围（基于 [P{N}] 标记）
- 章节 title 必须使用原文精确文字
- 如果没有某种类型，返回空数组 []
- 页码标记如 [P1][P2] 仅用于定位，不要在 title 中包含它们

=== 以下是书籍全文 ===

{book_text}"""


def _build_page_marked_text(markdown: str, content_list: list) -> str:
    """
    构建带页码标记的全文。
    在每页起始位置插入 [P{N}] 标记。
    """
    if not content_list:
        # 没有 content_list，退化为按行分段
        return markdown

    # 按页组织内容
    pages = {}
    for block in content_list:
        page_idx = block.get("page_idx", 0)
        if page_idx not in pages:
            pages[page_idx] = []
        pages[page_idx].append(block)

    # 构建带页码标记的文本
    lines = []
    for page_num in sorted(pages.keys()):
        blocks = pages[page_num]
        lines.append(f"\n[P{page_num + 1}]\n")  # page_idx 从0开始

        for block in blocks:
            block_type = block.get("type", "")
            text = ""

            if block_type == "text" or block_type == "title":
                text = block.get("text", "")
                level = block.get("text_level", 0)
                if level > 0:
                    prefix = "#" * min(level, 6)
                    text = f"{prefix} {text}"
            elif block_type == "paragraph":
                text = block.get("text", "")
            elif block_type == "image":
                text = f"[图片: {block.get('image_caption', [''])[0] if block.get('image_caption') else ''}]"
            elif block_type == "table":
                body = block.get("table_body", "")
                text = f"[表格]\n{body}\n[/表格]"
            elif block_type == "list":
                items = block.get("list_items", [])
                text = "\n".join(f"- {item}" for item in items)
            elif block_type in ("header", "footer", "page_number", "aside_text", "page_footnote"):
                # 页眉页脚标记但不剔除（留给 DeepSeek 判断）
                text = block.get("text", "")
            else:
                text = block.get("text", "")

            if text.strip():
                lines.append(text)

    return "\n".join(lines)


def _clean_json_response(raw: str) -> str:
    """从 DeepSeek 响应中提取 JSON"""
    # 尝试匹配 ```json ... ``` 块
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        return match.group(1)

    # 尝试匹配 ``` ... ``` 块
    match = re.search(r"```\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        return match.group(1)

    # 尝试匹配裸 JSON
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return match.group(0)

    raise ValueError(f"无法从响应中提取JSON: {raw[:500]}...")


def analyze_structure(markdown: str, content_list: list, book_name: str = "") -> dict:
    """
    用 DeepSeek 分析书籍结构。

    Returns:
        dict: 包含 metadata, front_matter, body, back_matter, noise_ranges
    """
    logger.info("Stage 2 开始: DeepSeek V4 Flash 结构分析")

    # 构建带页码标记的全文
    book_text = _build_page_marked_text(markdown, content_list)
    text_len = len(book_text)
    logger.info(f"  全文长度: {text_len:,} 字符 (约 {text_len//2:,} tokens)")

    if text_len > 900_000:
        logger.warning(f"  文本过长({text_len:,}字符)，可能超出上下文限制，将截断")
        book_text = book_text[:900_000]

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    user_prompt = USER_PROMPT_TEMPLATE.replace("{book_text}", book_text)

    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=16384,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
        )

        raw_output = resp.choices[0].message.content
        usage = resp.usage
        logger.info(
            f"  DeepSeek 响应: {len(raw_output):,} 字符, "
            f"消耗 {usage.prompt_tokens:,} input + {usage.completion_tokens:,} output tokens"
        )

    except Exception as e:
        logger.error(f"  DeepSeek API 调用失败: {e}")
        raise

    # 解析 JSON
    json_str = _clean_json_response(raw_output)
    try:
        structure = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"  JSON 解析失败: {e}")
        logger.error(f"  原始响应前500字符: {raw_output[:500]}")
        raise

    # 验证必要字段
    for key in ("metadata", "front_matter", "body", "back_matter"):
        if key not in structure:
            logger.warning(f"  DeepSeek 输出缺少字段 '{key}'，补为空")
            structure[key] = [] if key != "metadata" else {}

    logger.info(
        f"  Stage 2 完成: {len(structure['body'])} 个章节, "
        f"{len(structure['front_matter'])} 个前页段落, "
        f"{len(structure['back_matter'])} 个后页段落"
    )

    return structure


def save_structure(structure: dict, output_dir: str) -> Path:
    """保存结构分析结果"""
    path = Path(output_dir) / "structure.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)
    logger.info(f"  结构分析已保存: {path}")
    return path
