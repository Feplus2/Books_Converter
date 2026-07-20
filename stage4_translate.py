"""
Stage 4: 全书翻译 — DeepSeek 分批 + 上下文 + 并发

设计原则（不是逐块机翻）：
- 按阅读顺序把连续 block 组成 ~6000 字符的批次，每批附带前文作上下文，
  DeepSeek 整批文学翻译，段落编号一一对应
- 每批注入书名/作者与已累积的译名表（人名地名全书一致）
- 公式、编号标记、脚注编号不译；图片/表格结构不动
- 并发 4 批；失败批次保留原文；每批落盘 translations.json 支持断点续翻
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

_BATCH_CHARS = 6000       # 每批原文字符数上限
_WORKERS = 4              # 并发批次数
_MAX_RETRIES = 3

# 可翻译的 block 类型（图片/表格/公式不译正文）
_TRANSLATABLE = {"text", "title", "list", "page_footnote",
                 "image_caption", "table_caption", "header"}

_SYS_PROMPT = """你是一位资深的文学图书翻译，正在翻译一本书。

书名: {title}
作者: {author}
目标语言: {target_lang}

翻译要求:
1. 文学级质量：地道、流畅、符合目标语言表达习惯，杜绝翻译腔
2. 严格保持条目对应：输入按 [N] 编号，输出 JSON 的 translations 里用同样的编号
3. 人名、地名、专用术语全书统一。已知译名表（必须沿用）:
{glossary}
4. 新出现的稳定译名请补充到 terms 里（只收人名/地名/重要术语，不要普通词组）
5. 公式、数字编号、脚注标记（如 ①、$^{{a}}$）、章节编号保持原样，不译不拆
6. 不要增删内容，不要写注释或感想

只输出 JSON: {{"translations": {{"1": "译文", "2": "译文"}}, "terms": {{"English": "译文"}}}}"""


def _clean_json_response(raw: str) -> str:
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError(f"无法从响应中提取JSON: {raw[:300]}...")


def _collect_items(content_list: list) -> list:
    """收集待译条目：[(key, text)]，key = content_list 索引（caption 用 .capN）"""
    items = []
    for idx, block in enumerate(content_list):
        btype = block.get("type", "")
        text = (block.get("text") or "").strip()
        if btype in _TRANSLATABLE and text:
            items.append((str(idx), text))
        # 图片/表格的 caption 字段单独译
        for field, tag in (("image_caption", "cap"), ("table_caption", "tcap")):
            caps = block.get(field)
            if isinstance(caps, list):
                for n, cap in enumerate(caps):
                    cap_text = str(cap).strip()
                    if cap_text:
                        items.append((f"{idx}.{tag}{n}", cap_text))
    return items


def _build_batches(items: list, batch_chars: int = _BATCH_CHARS) -> list:
    """按字符量把条目组成批次（保持原文顺序）"""
    batches = []
    cur = []
    cur_len = 0
    for key, text in items:
        cur.append((key, text))
        cur_len += len(text)
        if cur_len >= batch_chars:
            batches.append(cur)
            cur = []
            cur_len = 0
    if cur:
        batches.append(cur)
    return batches


def _translate_batch(client: OpenAI, model: str, sys_prompt: str,
                     batch: list, context: str, _depth: int = 0) -> tuple[dict, dict]:
    """翻译一批，返回 ({key: 译文}, {英文: 中文} 译名)。失败返回 ({}, {})"""
    user = ""
    if context:
        user += f"以下为上下文（仅供理解，不要翻译）:\n{context}\n\n以下为要翻译的内容:\n"
    for n, (key, text) in enumerate(batch, 1):
        user += f"[{n}] {text}\n"

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user},
                ],
                max_tokens=16384,
                temperature=0.3,
                extra_body={"thinking": {"type": "disabled"}},
            )
            raw = resp.choices[0].message.content
            result = json.loads(_clean_json_response(raw))
            translations = result.get("translations", {})
            terms = result.get("terms", {})
            # 序号 → key
            out = {}
            for n, (key, _text) in enumerate(batch, 1):
                zh = translations.get(str(n))
                if isinstance(zh, str) and zh.strip():
                    out[key] = zh.strip()
            return out, terms if isinstance(terms, dict) else {}
        except Exception as e:
            last_err = e
            time.sleep((attempt + 1) * 8)

    # 重试仍失败：拆半递归（超长/含特殊字符的批次自救）
    if _depth < 2 and len(batch) > 1:
        mid = len(batch) // 2
        logger.info(f"  批次重试失败，拆半重试（{len(batch)} → {mid}+{len(batch) - mid}）")
        out1, terms1 = _translate_batch(client, model, sys_prompt,
                                        batch[:mid], context, _depth + 1)
        out2, terms2 = _translate_batch(client, model, sys_prompt,
                                        batch[mid:], context, _depth + 1)
        return {**out1, **out2}, {**terms1, **terms2}

    logger.warning(f"  批次翻译失败（{len(batch)} 条，保留原文）: {last_err}")
    return {}, {}


def translate_book(content_list: list, meta: dict, work_dir: str,
                   target_lang: str = "zh", progress=None) -> dict:
    """全书翻译主入口。

    Args:
        content_list: MinerU 的 block 列表
        meta: structure 的 metadata（书名/作者用于提示词与译名表种子）
        work_dir: 工作目录（translations.json 断点续翻）
        target_lang: 目标语言（默认 zh 中文）
        progress: 进度回调

    Returns:
        {"translations": {key: 译文}, "title_zh": 书名译文, "glossary": {...}}
    """
    _report = progress or (lambda *a, **kw: None)
    work_dir = Path(work_dir)
    out_path = work_dir / "translations.json"

    # 断点续翻：加载已有进度
    done = {}
    glossary = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            done = saved.get("translations", {})
            glossary = saved.get("glossary", {})
            logger.info(f"  断点续翻: 已有 {len(done)} 条译文")
        except Exception:
            pass

    items = _collect_items(content_list)
    todo = [(k, t) for k, t in items if k not in done]
    logger.info(f"  翻译: {len(items)} 条待译（新增 {len(todo)} 条）")

    batches = _build_batches(todo)
    title = meta.get("title", "")
    author = ", ".join(meta.get("authors") or []) or "Unknown"
    lang_name = {"zh": "简体中文", "en": "English"}.get(target_lang, target_lang)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    def make_sys() -> str:
        gloss = "\n".join(f"  {en} = {zh}" for en, zh in sorted(glossary.items())[:60])
        return _SYS_PROMPT.format(title=title, author=author,
                                  target_lang=lang_name,
                                  glossary=gloss or "  （暂无）")

    contexts = {}
    for bi, batch in enumerate(batches):
        # 前一批的最后一条作为上下文
        if bi > 0 and batches[bi - 1]:
            contexts[bi] = batches[bi - 1][-1][1][:400]
        else:
            contexts[bi] = ""

    n_done_batches = 0
    lock = __import__("threading").Lock()

    def work(bi: int):
        batch = batches[bi]
        with lock:
            sys_prompt = make_sys()
        out, terms = _translate_batch(client, DEEPSEEK_MODEL, sys_prompt,
                                      batch, contexts[bi])
        with lock:
            done.update(out)
            glossary.update({str(k): str(v) for k, v in terms.items()
                             if isinstance(k, str) and isinstance(v, str)})
            # 每批落盘（断点续翻）
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"translations": done, "glossary": glossary},
                          f, ensure_ascii=False)
        return bi, len(out)

    total = len(batches)
    if total:
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            futs = {ex.submit(work, bi): bi for bi in range(total)}
            for fut in as_completed(futs):
                try:
                    bi, n = fut.result()
                except Exception as e:
                    logger.warning(f"  批次 {futs[fut] + 1} 异常: {e}")
                    n = 0
                n_done_batches += 1
                _report(f"翻译批次 {n_done_batches}/{total}（{n} 条）",
                        n_done_batches / total)

    # 书名翻译（顺带，便宜）
    title_zh = meta.get("title_zh") or ""
    if not title_zh and title and target_lang == "zh":
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content":
                           f"把书名翻译成中文，只输出译名，不要解释:\n{title}"}],
                max_tokens=64, temperature=0.2,
                extra_body={"thinking": {"type": "disabled"}},
            )
            title_zh = (resp.choices[0].message.content or "").strip().strip("《》")
        except Exception:
            pass

    logger.info(f"  翻译完成: {len(done)} 条, 译名表 {len(glossary)} 条, "
                f"书名《{title_zh or title}》")
    return {"translations": done, "title_zh": title_zh, "glossary": glossary}


__all__ = ["translate_book"]
