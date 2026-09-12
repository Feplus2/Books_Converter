"""T2 目录提取：民法总论目录页（scan p6-13，8 页）→ 结构化 toc_entries。
模式：(a) 批量——8 页图一次调用（上限能力）；(b) 逐页——模拟增量阅读逐页提取后合并。
真值 = structure.json 的 173 条 toc_entries（已经锚点系统验证过：21 单元=6编15章）。

指标：条目召回（文本+页码双对）、层级准确率、形状栈一致性（同级同类不漂移）。
用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t2_toc [--mode batch|page]
"""
import json
import re
import sys

from .common import PDFS, PAGES, RUNS, norm
from .pagerender import render_png
from .vlm_client import chat

DPI = 150
TOC_PAGES = list(range(6, 19))  # p6-9 简目 + p10-18 详目
STRUCTURE_JSON = r"D:\My_Library\民法总论\民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk)\structure.json"
OUT = RUNS / "t2"
OUT.mkdir(exist_ok=True)

PROMPT_BATCH = """这是一本法学著作的目录页扫描图（共 {n} 页，按顺序给出）。请提取全部目录条目，输出 JSON 数组（不要其他文字）：
[{{"text": "条目标题（剥掉页码与前导点线）", "level": 整数层级, "page": 印刷页码整数或null}}, ...]

要求：
1. 保持目录原顺序，不要遗漏任何条目，不要输出目录之外的任何内容。
2. 层级规则：编/部分=1，章=2，节=3，小节=4，更细=5。**同一类标题必须同一 level**：所有"第X编"同层级、所有"第X章"同层级、所有"第X节"同层级，绝不允许前后漂移。
3. 页码只取条目末尾的印刷页码；罗马数字或无法识别给 null。
4. 标题里有圈码脚注标记（①等）剥掉。"""

PROMPT_PAGE = """这是一本书的目录页中的**一页**扫描图。请提取本页全部目录条目，输出 JSON 数组（不要其他文字）：
[{{"text": "条目标题（剥掉页码与前导点线）", "level": 整数层级, "page": 印刷页码整数或null}}, ...]

要求：
1. 只提取本页条目，保持原顺序。
2. 层级规则：编/部分=1，章=2，节=3，小节=4，更细=5。同一类标题必须同一 level。
3. 若本页不是目录页或没有条目，输出 []。"""

DEFAULT_MODELS = [
    ("zai", "glm-5.3-flash", "low"),
    ("dashscope", "qwen3-vl-flash", "off"),
    ("deepseek", "deepseek-flash", "off"),
    ("ark", "doubao-seed-2-1-turbo-260628", "off"),
]


def page_png(p: int) -> bytes:
    f = PAGES / f"minfa_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDFS / "minfa.pdf", p, DPI))
    return f.read_bytes()


def truth_entries() -> list[dict]:
    st = json.load(open(STRUCTURE_JSON, encoding="utf-8"))
    return st["toc_entries"]


def parse_entries(content: str) -> list[dict]:
    m = re.search(r"\[.*\]", content or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [e for e in arr if isinstance(e, dict) and e.get("text")]
    except Exception:
        return []


def score(pred: list[dict], truth: list[dict]) -> dict:
    tmap = {}
    for t in truth:
        tmap.setdefault(norm(t["text"]), t)
    hit = page_ok = level_ok = 0
    used = set()
    for p in pred:
        k = norm(p.get("text", ""))
        t = tmap.get(k)
        if t is None:
            # 宽容匹配：互为子串（截断/赘字）
            cands = [tk for tk in tmap if tk not in used and (k in tk or tk in k) and min(len(k), len(tk)) >= 4]
            if cands:
                k = max(cands, key=len)
                t = tmap[k]
        if t is not None:
            used.add(k)
            hit += 1
            if p.get("page") == t.get("page"):
                page_ok += 1
            if p.get("level") == t.get("level"):
                level_ok += 1
    # 形状栈一致性：按编号词形分组，组内 level 是否唯一
    groups: dict[str, set] = {}
    for p in pred:
        txt = p.get("text", "")
        g = ("编" if re.match(r"^第[一二三四五六七八九十\d]+编", txt)
             else "章" if re.match(r"^第[一二三四五六七八九十\d]+章", txt)
             else "节" if re.match(r"^第[一二三四五六七八九十\d]+节", txt)
             else None)
        if g:
            groups.setdefault(g, set()).add(p.get("level"))
    drift = {g: sorted(v, key=str) for g, v in groups.items() if len(v) > 1}
    return {
        "truth_n": len(truth), "pred_n": len(pred),
        "recall": round(hit / max(len(truth), 1), 4),
        "page_acc": round(page_ok / max(hit, 1), 4),
        "level_acc": round(level_ok / max(hit, 1), 4),
        "level_drift_groups": drift,
    }


def main() -> None:
    mode = "batch"
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--mode="):
            mode = a.split("=")[1]
        elif a.startswith("--only="):
            only = a.split("=")[1]
    models = [m for m in DEFAULT_MODELS if not only or only in m[1]]
    truth = truth_entries()
    log = RUNS / "t2.jsonl"
    rows = []
    for prov, model, lv in models:
        if mode == "batch":
            r = chat(prov, model, PROMPT_BATCH.format(n=len(TOC_PAGES)),
                     images=[page_png(p) for p in TOC_PAGES], reasoning=lv,
                     max_tokens=32768, log=str(log))
            tag = f"{prov}_{model.replace('/', '-')}_batch"
            if "error" in r:
                print(f"{tag}: ERROR {r['error'][:120]}")
                rows.append({"tag": tag, "error": r["error"][:200]})
                continue
            (OUT / f"{tag}.json").write_text(r["content"] or "", encoding="utf-8")
            pred = parse_entries(r["content"])
        else:
            pred = []
            for p in TOC_PAGES:
                r = chat(prov, model, PROMPT_PAGE, images=[page_png(p)], reasoning=lv,
                         max_tokens=16384, log=str(log))
                if "error" not in r:
                    pred.extend(parse_entries(r["content"]))
            tag = f"{prov}_{model.replace('/', '-')}_page"
            (OUT / f"{tag}.json").write_text(json.dumps(pred, ensure_ascii=False, indent=1), encoding="utf-8")
        sc = score(pred, truth)
        rows.append({"tag": tag, **sc})
        print(f"{tag}: recall={sc['recall']} page_acc={sc['page_acc']} "
              f"level_acc={sc['level_acc']} drift={sc['level_drift_groups']} pred_n={sc['pred_n']}")
    (OUT / f"rows_{mode}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
