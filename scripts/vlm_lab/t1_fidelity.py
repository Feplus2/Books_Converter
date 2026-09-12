"""T1 单页保真：20 页分层样本 × 多模型——三向对比（VLM vs MinerU 缓存 vs 文本层[仅 qft]）。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t1_fidelity [--models prov/model:lv,...]
"""
import json
import sys

from .common import PDFS, PAGES, RUNS, TRUTH
from .evalutil import diff_windows, page_scores, split_out
from .pagerender import render_png, text_layer
from .prompts import TRANSCRIBE
from .vlm_client import chat

DPI = 150
SAMPLES = (
    [("minfa", p, "toc") for p in (7, 11)]
    + [("minfa", p, "chapter") for p in (20, 110)]
    + [("minfa", p, "footnote") for p in (117, 179, 280, 289, 377, 490)]
    + [("minfa", p, "image") for p in (33, 54, 83)]
    + [("minfa", p, "plain") for p in (152, 215, 237, 255, 295)]
    + [("qft", p, "formula") for p in (57, 205)]
)
DEFAULT_MODELS = [
    ("zai", "glm-5.3-flash", "low"),
    ("dashscope", "qwen3-vl-flash", "off"),
    ("deepseek", "deepseek-flash", "off"),
    ("ark", "doubao-seed-2-1-turbo-260628", "off"),
    ("cherryin", "google/gemini-3-flash-preview", None),
]
OUT = RUNS / "t1"
OUT.mkdir(exist_ok=True)


def page_png(book: str, p: int) -> bytes:
    f = PAGES / f"{book}_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDFS / f"{book}.pdf", p, DPI))
    return f.read_bytes()


def truth_of(book: str, p: int) -> tuple[str, str]:
    t = json.loads((TRUTH / book / f"p{p:03d}.json").read_text(encoding="utf-8"))
    return "\n".join(t["text"]), "\n".join(t["footnotes"])


def parse_models(argv: list[str]) -> list:
    for a in argv[1:]:
        if a.startswith("--models="):
            out = []
            for spec in a[len("--models="):].split(","):
                prov_model, _, lv = spec.partition(":")
                prov, _, model = prov_model.partition("/")
                out.append((prov, model, lv or None))
            return out
    return DEFAULT_MODELS


def main() -> None:
    models = parse_models(sys.argv)
    log = RUNS / "t1.jsonl"
    rows = []
    for prov, model, lv in models:
        for book, p, kind in SAMPLES:
            t_body, t_fn = truth_of(book, p)
            r = chat(prov, model, TRANSCRIBE, images=[page_png(book, p)], reasoning=lv, log=str(log))
            tag = f"{prov}_{model.replace('/', '-')}_{book}p{p}_{lv or 'auto'}"
            if "error" in r:
                rows.append({"tag": tag, "book": book, "page": p, "kind": kind, "error": r["error"][:200]})
                print(f"{tag}: ERROR {r['error'][:120]}")
                continue
            (OUT / f"{tag}.txt").write_text(r["content"] or "", encoding="utf-8")
            body, fns = split_out(r["content"])
            sc = page_scores(body, fns, t_body, t_fn)
            from .evalutil import direction_counts
            sc.update(direction_counts(body, t_body))
            u = r.get("usage") or {}
            row = {"tag": tag, "book": book, "page": p, "kind": kind,
                   "lat": r["latency_s"], "finish": r.get("finish_reason"),
                   "tin": u.get("prompt_tokens"), "tout": u.get("completion_tokens"), **sc}
            if book == "qft":
                tl = text_layer(PDFS / f"{book}.pdf", p)
                row["cer_body_vs_textlayer"] = round(
                    page_scores(body, "", tl, "")["cer_body"], 4)
            rows.append(row)
            print(f"{tag}: {row['lat']}s cer_body={row['cer_body']} cer_fn={row['cer_fn']}")
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    # 汇总：加权 CER（总编辑距/总真值字符）按模型
    agg: dict = {}
    for r in rows:
        if "error" in r:
            continue
        a = agg.setdefault(r["tag"].split("_minfa")[0].split("_qft")[0],
                           {"e": 0, "c": 0, "ef": 0, "cf": 0, "lat": [], "tin": 0, "tout": 0, "n": 0})
        a["e"] += r["edits_body"]; a["c"] += r["chars_truth"]
        a["ef"] += r["edits_fn"]; a["cf"] += r["chars_fn_truth"]
        a["lat"].append(r["lat"]); a["tin"] += r["tin"] or 0; a["tout"] += r["tout"] or 0; a["n"] += 1
    print("\n=== 加权汇总 ===")
    for k, a in agg.items():
        w = a["e"] / max(a["c"], 1); wf = a["ef"] / max(a["cf"], 1) if a["cf"] else None
        lat = sorted(a["lat"])
        print(f"{k}: w_cer={w:.4f} w_cer_fn={wf and round(wf,4)} "
              f"lat_p50={lat[len(lat)//2]}s tin_avg={a['tin']//a['n']} tout_avg={a['tout']//a['n']}")


if __name__ == "__main__":
    main()
