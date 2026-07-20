"""
Stage 2 (Hybrid 引擎): DeepSeek 全权 + TOC 锚点/形状栈

与 Popo 引擎的区别只在"标注 blocks 如何得到"：
- Popo 引擎：本地 4B VLM，看页面图像做判断（需要 GPU，慢，可能崩）
- Hybrid 引擎：DeepSeek 云端 API，只看文本做判断（零本地算力，快，便宜）

四项任务全部复用 popo 包的筛选器/prompt/解析器，仅把模型调用换成
DeepSeek 文本推理（多线程）。层级最终仍由 TOC 锚点 + 形状栈确定
（stage2_common.finish_structure），LLM 不做全局结构判断——
"LLM 回答局部问题，代码拼装全局结构"。
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

import popo
from popo import inference as pi
from config import CHUNK_SIZE, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from stage2_common import finish_structure, save_structure  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)

_WORKERS = 4          # DeepSeek API 并发数
_MAX_RETRIES = 3


def _deepseek_generate(client: OpenAI, prompt: str, system: str = "") -> str:
    """DeepSeek 文本推理，带重试。输入为 popo 风格的任务 prompt（纯文本）。

    popo 的 <|id|>N<|level|>M 输出约定是其微调产物，DeepSeek 没见过，
    必须用 system prompt 显式规定输出格式，否则它会写分析散文。
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=8192,
                temperature=0.1,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            last_err = e
            time.sleep((attempt + 1) * 10)
    raise RuntimeError(f"DeepSeek 推理重试 {_MAX_RETRIES} 次仍失败: {last_err}")


# 各任务的输出格式约定（popo 微调格式的 DeepSeek 显式版）
_SYS_CONTD = """你是文档结构分析模型。用户会给出一组文本块（候选的跨页段落拼接对），每行格式：
<|id|>编号<|page|>页码<|box|>坐标<|content|>文本片段

任务：判断哪些相邻块属于同一段落、被分页截断（应当拼接）。
判断依据：上段末尾无终止标点、语义明显未完、下段是其自然延续。

输出要求：只输出应当拼接的对，每行一个，格式严格为：
<|src_id|>上段编号<|tgt_id|>下段编号
不要输出任何解释、标题或额外内容。没有应拼接的就不输出。"""

_SYS_TITLE = """你是文档结构分析模型。用户会给出一组候选标题块，每行格式：
<|id|>编号<|page|>页码<|box|>坐标<|content|>文本

任务：判断每块是否为书籍的结构标题，并给出层级。
- 层级 1 = 最高层（编/篇/卷/部/Part），2 = 章，3 = 节，4 及更深 = 小节/细目
- 不是结构标题的（思考题、定义、列表项、正文短句、页眉页脚）输出 -1

输出要求：每个候选一行，格式严格为：
<|id|>编号<|level|>层级
不要输出任何解释或额外内容。"""

_SYS_IMAGE = """你是文档结构分析模型。用户会给出文本/图片/表格/图注/脚注块，每行格式：
<|id|>编号<|type|>类型<|page|>页码<|box|>坐标<|content|>文本或None

任务：判断每个图注(image_caption/table_caption)/脚注(image_footnote/table_footnote)
块归属于哪个图片(image)或表格(table)块。

输出要求：只输出关联对，每行一个，格式严格为：
<|src_id|>图注编号<|tgt_id|>图片编号
不要输出任何解释或额外内容。无关联就不输出。"""

_SYS_TABLE = """你是文档结构分析模型。用户会给出上一页末的表格和下一页首的表格，
判断它们是否是同一张被分页截断的表。
若是，给出下表首行各单元格应并入上表末行的标记列表（1=并入，0=不并入）。

输出要求：只输出一个 Python 列表，如 [0, 1, 0]。不是同一张表就输出空列表 []。
不要输出任何解释。"""


def _run_chunks(task: str, chunks_data: list, prompt_builder, parser,
                gen, progress) -> list:
    """对一项任务的全部分块并发推理，按分块序号返回解析结果列表。

    prompt_builder(chunk) -> str；parser(raw_text) -> list[dict]。
    """
    n = len(chunks_data)
    results = [None] * n

    def work(item):
        idx, (rng, chunk) = item
        raw = gen(prompt_builder(chunk))
        return idx, parser(raw)

    done = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futs = {ex.submit(work, it): it for it in chunks_data}
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                idx, parsed = fut.result()
                results[idx] = parsed
            except Exception as e:
                failed += 1
                logger.warning(f"  {task} 分块 {it[0] + 1} 失败: {e}")
                results[it[0]] = []
            done += 1
            progress(f"{task} 分块 {done}/{n}", done / n)

    if n > 0 and failed == n:
        raise RuntimeError(f"{task}: 全部 {n} 个分块均失败，中止")
    return results


def run_inference_hybrid(content_list: list, book_name: str,
                         progress=None, chunk_size: int = CHUNK_SIZE) -> list:
    """DeepSeek 版四项后处理：输出与 popo.run_inference 同构的标注 blocks。"""
    _report = progress or (lambda *a, **kw: None)
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    _gen_contd = lambda p: _deepseek_generate(client, p, _SYS_CONTD)   # noqa: E731
    _gen_title = lambda p: _deepseek_generate(client, p, _SYS_TITLE)   # noqa: E731
    _gen_image = lambda p: _deepseek_generate(client, p, _SYS_IMAGE)   # noqa: E731
    _gen_table = lambda p: _deepseek_generate(client, p, _SYS_TABLE)   # noqa: E731

    # ── 转换 + 赋号（与 popo.run_inference 相同） ──
    pages = popo.content_list_to_pages(content_list, doc_id=book_name)
    pi.validate_pages(pages, doc_key=book_name)

    doc_blocks = []
    idx = 1
    for page_num in sorted(pages, key=int):
        for block in pages[page_num]:
            block["page"] = int(page_num)
            block["id"] = idx
            idx += 1
        doc_blocks.extend(pages[page_num])
    for block in doc_blocks:
        block["contd"] = -1
        block["level"] = -1
        block["image"] = -1
        if block["type"] == "table":
            block["table_merge"] = -1

    # ── 候选筛选（popo 启发式，本地免费） ──
    contd = pi.add_contd(pi.filter_contd(doc_blocks))
    title = pi.add_title(pi.filter_title(doc_blocks))
    image_judge_blocks, large_block_linking = pi.filter_image(doc_blocks)
    image = pi.add_image(image_judge_blocks)
    merge_inputs = pi.filter_table_merge(doc_blocks)

    # ── 任务 1/4: 跨页段落拼接 ──
    ranges_c, chunks_c = pi.adaptive_chunk(pi.parse_string_notype(contd),
                                           chunk_size=chunk_size)
    items_c = list(enumerate(zip(ranges_c, chunks_c)))
    ranges_t, chunks_t = pi.adaptive_chunk(pi.parse_string_notype(title),
                                           chunk_size=chunk_size)
    items_t = list(enumerate(zip(ranges_t, chunks_t)))
    ranges_i, chunks_i = pi.adaptive_chunk(pi.parse_string_type(image),
                                           chunk_size=chunk_size)
    items_i = list(enumerate(zip(ranges_i, chunks_i)))

    # 进度按全书分块统一记账（避免单任务各自 0→100% 导致进度条先到 95% 卡死）
    n1, n2, n3 = len(items_c), len(items_t), len(items_i)
    n4 = max(len(merge_inputs), 1)
    total = max(n1 + n2 + n3 + n4, 1)
    s1, s2, s3 = n1 / total, (n1 + n2) / total, (n1 + n2 + n3) / total

    def staged(start, end):
        def rep(msg, frac):
            _report(msg, start + frac * (end - start))
        return rep

    def contd_prompt(chunk):
        texts = [f"<|id|>{b['id']}<|page|>{b['page']}<|box|>{b['box']}<|content|>{b['content']}"
                 for b in chunk]
        return "Truncation Detection: " + "\n".join(texts)

    _report(f"阶段 1/4 跨页段落拼接: {n1} 个分块")
    contd_res = _run_chunks("跨页段落拼接", items_c, contd_prompt,
                            lambda raw: pi.extract_label1(
                                raw.replace("<|from|>", "<|src_id|>").replace("<|to|>", "<|tgt_id|>")),
                            _gen_contd, staged(0, s1))
    contd_pairs = []
    for pairs in contd_res:
        for pair in pairs:
            if pair not in contd_pairs:
                contd_pairs.append(pair)

    # ── 任务 2/4: 标题层级（首票；最终层级由锚点+形状栈确定） ──
    def title_prompt(chunk):
        texts = [f"<|id|>{b['id']}<|page|>{b['page']}<|box|>{b['box']}<|content|>{b['content']}"
                 for b in chunk]
        return "Title Level Analysis: " + "\n".join(texts)

    _report(f"阶段 2/4 标题层级: {n2} 个分块")
    title_res = _run_chunks("标题层级", items_t, title_prompt,
                            pi.extract_label2, _gen_title, staged(s1, s2))

    # 跨分块首票合并（同 popo 的 bias 逻辑，仅作首票）
    order_res = {}
    for (_idx, (rng, _chunk)), pairs in zip(items_t, title_res):
        order_res[rng[0] + rng[1]] = pairs
    title_pairs = []
    for _key, id_pairs in sorted(order_res.items()):
        bias = []
        for pair in id_pairs:
            for exist in title_pairs:
                if pair["id"] == exist["id"]:
                    if pair["level"] < 0 or exist["level"] < 0:
                        pair["level"] = exist["level"] = -1
                    else:
                        bias.append(pair["level"] - exist["level"])
                        pair["level"] = exist["level"]
                    break
        avg_bias = round(sum(bias) / len(bias)) if bias else 0
        for pair in id_pairs:
            if not any(pair["id"] == e["id"] for e in title_pairs):
                pair["level"] = pair["level"] - avg_bias if pair["level"] > 0 else pair["level"]
                title_pairs.append(pair)

    # ── 任务 3/4: 图文关联 ──
    def image_prompt(chunk):
        texts = [f"<|id|>{b['id']}<|type|>{b['type']}<|page|>{b['page']}<|box|>{b['box']}<|content|>{b['content']}"
                 for b in chunk]
        return "Image-Text Correlation Analysis: " + "\n".join(texts)

    _report(f"阶段 3/4 图文关联: {n3} 个分块")
    image_res = _run_chunks("图文关联", items_i, image_prompt,
                            pi.extract_label1, _gen_image, staged(s2, s3))
    image_pairs = []
    for pairs in image_res:
        for pair in pairs:
            if pair not in image_pairs:
                image_pairs.append(pair)

    # ── 结果回写（与 popo 相同） ──
    for pair in contd_pairs:
        try:
            doc_blocks[pair["src_id"]]["contd"] = pair["tgt_id"] + 1
        except Exception:
            pass
    for pair in image_pairs:
        try:
            doc_blocks[pair["src_id"]]["image"] = pair["tgt_id"] + 1
        except Exception:
            pass
    for pair in title_pairs:
        try:
            doc_blocks[pair["id"]]["level"] = pair["level"]
        except Exception:
            pass
    for src, tgt in large_block_linking.items():
        doc_blocks[src]["image"] = tgt + 1

    # ── 任务 4/4: 跨页表格合并 ──
    _report(f"阶段 4/4 跨页表格合并: {len(merge_inputs)} 个候选")
    for i, mi in enumerate(merge_inputs):
        prompt = pi.add_table_merge(mi["upper_row_ss"], mi["lower_row_ss"])
        try:
            raw = _gen_table(prompt)
            cell_list = pi.extract_last_coordinates(raw)
            if cell_list and isinstance(cell_list, list) and len(cell_list) > 0:
                doc_blocks[mi["table1_idx"]]["table_merge"] = doc_blocks[mi["table2_idx"]]["id"]
                doc_blocks[mi["table2_idx"]]["table_merge"] = doc_blocks[mi["table1_idx"]]["id"]
                doc_blocks[mi["table1_idx"]]["cell_list"] = cell_list
                doc_blocks[mi["table2_idx"]]["cell_list"] = cell_list
        except Exception as e:
            logger.warning(f"跨页表格合并推理失败: {e}")
        _report(f"跨页表格合并 {i + 1}/{len(merge_inputs)}",
                s3 + (i + 1) / max(len(merge_inputs), 1) * (1 - s3))

    logger.info(f"  Hybrid 推理完成: {len(doc_blocks)} 个标注 block")
    return doc_blocks


def analyze_structure_hybrid(content_list: list, book_name: str, work_dir: str,
                             progress=None, max_pages: int | None = None) -> dict:
    """Hybrid 引擎的结构分析主入口（无需 PDF，无需 GPU）。

    与早期 Popo 引擎的区别仅在标注 blocks 的来源（DeepSeek 代替
    本地 VLM），此后的轻量兜底/锚点校正/文档树完全一致。
    """
    _report = progress or (lambda *a, **kw: None)
    work_dir = Path(work_dir)

    if max_pages:
        content_list = [b for b in content_list
                        if b.get("page_idx", 0) < max_pages]
        logger.info(f"  切片模式: 只处理前 {max_pages} 页 "
                    f"({len(content_list)} blocks)")

    _report("Hybrid: DeepSeek 四项后处理推理...")
    blocks = run_inference_hybrid(content_list, book_name, progress=_report)

    return finish_structure(blocks, content_list, book_name, work_dir,
                            engine="hybrid", progress=progress)


__all__ = ["analyze_structure_hybrid", "save_structure"]
