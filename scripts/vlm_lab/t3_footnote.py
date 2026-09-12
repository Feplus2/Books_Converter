"""T3 脚注重建 A/B：30 个脚注密集页（民法总论）。
A 案 = 模型直接配对（JSON 显式 marker↔注文）；
B 案 = 模型只转写（脚注留在页底原位），规则层按标记配对（失败方向=页底保留）。

自动指标：footnote 召回（按标记对齐）、流浪率（VLM 多出）、配对注文 CER、
正文内标记保留率。真值 = MinerU 缓存 page_footnote 块。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t3_footnote [--models ...] [--pages 30]
"""
import json
import re
import sys

from .common import PDFS, PAGES, RUNS, TRUTH, edit_distance, norm
from .pagerender import render_png
from .vlm_client import chat

DPI = 150
MARKER_RE = re.compile(r"[①-⑳]")

PROMPT_A = """你是高精度图书数字化引擎。输入是一页法学学术著作的扫描图像。这页含有若干脚注（正文中的圈码①②③…与页面底部的对应注文）。

任务：输出 JSON（不要任何其他文字）：
{"body": "正文文字（逐字转写，脚注圈码原样保留在正文位置；忽略页眉页码）",
 "footnotes": [{"marker": "①", "text": "该圈码对应的页底注文逐字转写"}, ...]}

要求：每个正文圈码都必须有对应 footnote 条目；注文逐字转写，不得概括或补全；页面无脚注则 footnotes 为空数组。"""

PROMPT_B = """你是高精度图书数字化转写引擎。输入是一页书的扫描图像，请逐字转写页面文字。

规则：
1. 只输出页面上实际存在的文字，逐字转写。不要补全、概括、翻译、改写。
2. 忽略页眉（书眉）、页脚、页码、装饰线。
3. 按页面阅读顺序输出：先正文，页面底部的脚注注文保持在它们原来的页底位置输出，每条注文单独一行。
4. 正文中的脚注圈码（①②③…）原样保留。

输出纯文本即可。"""

DEFAULT_MODELS = [
    ("zai", "glm-5.3-flash", "low"),
    ("deepseek", "deepseek-flash", "off"),
    ("ark", "doubao-seed-2-1-turbo-260628", "off"),
]
OUT = RUNS / "t3"
OUT.mkdir(exist_ok=True)


def page_png(book: str, p: int) -> bytes:
    f = PAGES / f"{book}_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDFS / f"{book}.pdf", p, DPI))
    return f.read_bytes()


def truth_pairs(p: int) -> dict[str, str]:
    t = json.loads((TRUTH / "minfa" / f"p{p:03d}.json").read_text(encoding="utf-8"))
    pairs = {}
    for fn in t["footnotes"]:
        fn = fn.strip()
        if fn and MARKER_RE.match(fn[0]):
            pairs[fn[0]] = fn[1:].strip()
    body_markers = MARKER_RE.findall("\n".join(t["text"]))
    return {"pairs": pairs, "body_markers": body_markers}


def parse_a(content: str) -> tuple[str, dict[str, str]]:
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return "", {}
    try:
        d = json.loads(m.group(0))
        return d.get("body", ""), {x.get("marker", ""): x.get("text", "") for x in d.get("footnotes", [])}
    except Exception:
        return "", {}


def parse_b(content: str) -> tuple[str, dict[str, str]]:
    """B 案规则配对：找连续页底注文区（以圈码开头的行），正文扫圈码，按标记配对。"""
    lines = (content or "").splitlines()
    pairs: dict[str, str] = {}
    body_lines = []
    for ln in lines:
        s = ln.strip()
        if s and MARKER_RE.match(s[0]):
            pairs[s[0]] = s[1:].strip()
        else:
            body_lines.append(ln)
    return "\n".join(body_lines), pairs


def score(vlm_pairs: dict, truth: dict) -> dict:
    tp = truth["pairs"]
    hit = set(vlm_pairs) & set(tp)
    e = sum(edit_distance(norm(vlm_pairs[m]), norm(tp[m])) for m in hit)
    c = sum(len(norm(tp[m])) for m in hit)
    return {
        "fn_truth": len(tp), "fn_vlm": len(vlm_pairs), "fn_hit": len(hit),
        "recall": round(len(hit) / max(len(tp), 1), 3),
        "stray": len(set(vlm_pairs) - set(tp)),
        "pair_cer": round(e / max(c, 1), 4) if hit else None,
    }


def main() -> None:
    models = DEFAULT_MODELS
    n_pages = 30
    for a in sys.argv[1:]:
        if a.startswith("--pages="):
            n_pages = int(a.split("=")[1])
    samples = json.loads((TRUTH / "minfa_samples.json").read_text(encoding="utf-8"))
    fn_all = sorted(samples["footnote_top"] + [117, 179, 280, 289, 377, 437, 37, 94, 95, 143, 169])
    # 补满 n_pages：从真值目录按脚注数排序取
    if len(set(fn_all)) < n_pages:
        dens = []
        for f in (TRUTH / "minfa").glob("p*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            if d["footnotes"]:
                dens.append((int(f.stem[1:]), len(d["footnotes"])))
        dens.sort(key=lambda x: -x[1])
        fn_all = sorted(set(fn_all) | {p for p, _ in dens})
    pages = sorted(set(fn_all))[:n_pages]
    print(f"T3 pages ({len(pages)}): {pages}")

    log = RUNS / "t3.jsonl"
    rows = []
    for prov, model, lv in models:
        for mode, prompt, parser in (("A", PROMPT_A, parse_a), ("B", PROMPT_B, parse_b)):
            for p in pages:
                truth = truth_pairs(p)
                r = chat(prov, model, prompt, images=[page_png("minfa", p)], reasoning=lv, log=str(log))
                tag = f"{prov}_{model.replace('/', '-')}_{mode}_p{p}"
                if "error" in r:
                    rows.append({"tag": tag, "page": p, "mode": mode, "error": r["error"][:200]})
                    print(f"{tag}: ERROR {r['error'][:110]}")
                    continue
                (OUT / f"{tag}.txt").write_text(r["content"] or "", encoding="utf-8")
                body, pairs = parser(r["content"])
                sc = score(pairs, truth)
                rows.append({"tag": tag, "page": p, "mode": mode, **sc,
                             "lat": r["latency_s"], "finish": r.get("finish_reason")})
                print(f"{tag}: recall={sc['recall']} stray={sc['stray']} pair_cer={sc['pair_cer']}")
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    agg: dict = {}
    for r in rows:
        if "error" in r:
            continue
        k = (r["tag"].rsplit("_p", 1)[0])
        a = agg.setdefault(k, {"t": 0, "h": 0, "s": 0, "v": 0, "n": 0, "cers": []})
        a["t"] += r["fn_truth"]; a["h"] += r["fn_hit"]; a["s"] += r["stray"]; a["v"] += r["fn_vlm"]; a["n"] += 1
        if r["pair_cer"] is not None:
            a["cers"].append(r["pair_cer"])
    print("\n=== 汇总（model_mode）===")
    for k, a in agg.items():
        cer_avg = sum(a["cers"]) / max(len(a["cers"]), 1)
        print(f"{k}: recall={a['h']}/{a['t']}={a['h']/max(a['t'],1):.3f} stray={a['s']} "
              f"pair_cer_avg={cer_avg:.4f} pages={a['n']}")


if __name__ == "__main__":
    main()
