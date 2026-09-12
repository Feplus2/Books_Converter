"""T4 图片区域定位：20 个插图页，VLM bbox（0-1000 归一化）vs MinerU 裁剪 bbox（同为 0-1000 千分位）。
指标：IoU≥0.5 匹配率（recall/precision）、最佳 IoU 均值；失配案例裁图人工复核。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t4_image_bbox
"""
import json
import re
import sys

from .common import PDFS, PAGES, RUNS, TRUTH
from .pagerender import render_png
from .vlm_client import chat

DPI = 150
IMAGE_PAGES = [33, 40, 44, 49, 54, 60, 73, 83, 92, 100, 106, 132, 144, 148, 150, 160, 178, 187, 198, 201]
OUT = RUNS / "t4"
OUT.mkdir(exist_ok=True)

PROMPT = """这是一页书的扫描图像。请定位页面中所有**插图/图表区域**（非文字的图片、示意图、照片、图表；不含页眉装饰线、不含纯文字表格），输出 JSON（不要其他文字）：
{"images": [{"bbox": [x1, y1, x2, y2]}, ...]}
坐标为归一化整数（0-1000 千分位），原点在页面左上角，x 向右 y 向下。没有插图则输出 {"images": []}。"""

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


def truth_bboxes(p: int) -> list[list[float]]:
    t = json.loads((TRUTH / "minfa" / f"p{p:03d}.json").read_text(encoding="utf-8"))
    return [b for b in t["images"] if b]


def parse_bboxes(content: str) -> list[list[float]]:
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
        out = []
        for x in d.get("images", []):
            b = x.get("bbox")
            if isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                out.append([float(v) for v in b])
        return out
    except Exception:
        return []


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main() -> None:
    log = RUNS / "t4.jsonl"
    rows = []
    for prov, model, lv in DEFAULT_MODELS:
        agg = {"t": 0, "hit": 0, "pred": 0, "pred_hit": 0, "ious": []}
        for p in IMAGE_PAGES:
            truth = truth_bboxes(p)
            if not truth:
                continue
            r = chat(prov, model, PROMPT, images=[page_png(p)], reasoning=lv,
                     max_tokens=4096, log=str(log))
            tag = f"{prov}_{model.replace('/', '-')}_p{p}"
            if "error" in r:
                rows.append({"tag": tag, "page": p, "error": r["error"][:200]})
                print(f"{tag}: ERROR {r['error'][:110]}")
                continue
            pred = parse_bboxes(r["content"])
            (OUT / f"{tag}.json").write_text(json.dumps(
                {"pred": pred, "truth": truth}, ensure_ascii=False), encoding="utf-8")
            matched_t, matched_p = set(), set()
            for i, pb in enumerate(pred):
                best, bj = 0.0, -1
                for j, tb in enumerate(truth):
                    v = iou(pb, tb)
                    if v > best:
                        best, bj = v, j
                if best >= 0.5 and bj not in matched_t:
                    matched_t.add(bj)
                    matched_p.add(i)
                    agg["ious"].append(best)
            agg["t"] += len(truth)
            agg["hit"] += len(matched_t)
            agg["pred"] += len(pred)
            agg["pred_hit"] += len(matched_p)
            rows.append({"tag": tag, "page": p, "truth_n": len(truth), "pred_n": len(pred),
                         "hit": len(matched_t)})
        rec = agg["hit"] / max(agg["t"], 1)
        prec = agg["pred_hit"] / max(agg["pred"], 1)
        miou = sum(agg["ious"]) / max(len(agg["ious"]), 1)
        print(f"== {prov}/{model}: recall@0.5={rec:.3f} ({agg['hit']}/{agg['t']}) "
              f"precision@0.5={prec:.3f} meanIoU={miou:.3f}")
        rows.append({"tag": f"{prov}_{model}", "summary": True, "recall": round(rec, 4),
                     "precision": round(prec, 4), "mean_iou": round(miou, 4)})
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
