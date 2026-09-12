"""T0 冒烟：3 页（脚注密集/普通正文/插图页）× 6 模型 × 思考档位矩阵。
目标：DeepSeek 分辨率判决、思考档开/低/高差异、图像 token 实计、基本转写质量。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t0_smoke
"""
import json
import re

from .common import PDFS, PAGES, RUNS, TRUTH, cer
from .pagerender import render_png
from .prompts import TRANSCRIBE
from .vlm_client import chat

PDF = PDFS / "minfa.pdf"
DPI = 150
PAGES_UNDER_TEST = [179, 152, 83]
MATRIX = [
    ("zai", "glm-5.3-flash", ["low", "high", "max"]),
    ("zai", "glm-4.6v-flash", ["off", "on"]),
    ("deepseek", "deepseek-flash", ["off", "low", "high"]),
    ("dashscope", "qwen3-vl-flash", ["off", "low"]),
    ("ark", "doubao-seed-1-6-flash-250828", ["off", "on"]),
    ("cherryin", "google/gemini-3-flash-preview", [None]),
]
OUT = RUNS / "t0"
OUT.mkdir(exist_ok=True)


def page_png(p: int) -> bytes:
    f = PAGES / f"minfa_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDF, p, DPI))
    return f.read_bytes()


def split_out(content: str) -> tuple[str, str]:
    if not content:
        return "", ""
    body = re.search(r"<body>(.*?)</body>", content, re.S)
    fns = re.search(r"<footnotes>(.*?)</footnotes>", content, re.S)
    b = body.group(1) if body else content
    f = fns.group(1) if fns else ""
    return b.replace("[图]", ""), f


def truth_of(p: int) -> tuple[str, str]:
    t = json.loads((TRUTH / "minfa" / f"p{p:03d}.json").read_text(encoding="utf-8"))
    return "\n".join(t["text"]), "\n".join(t["footnotes"])


def main() -> None:
    rows = []
    log = RUNS / "t0.jsonl"
    for prov, model, levels in MATRIX:
        for p in PAGES_UNDER_TEST:
            img = page_png(p)
            t_body, t_fn = truth_of(p)
            for lv in levels:
                tag = f"{prov}_{model.replace('/', '-')}_p{p}_{lv or 'auto'}"
                r = chat(prov, model, TRANSCRIBE, images=[img], reasoning=lv,
                         log=str(log))
                if "error" in r:
                    print(f"{tag}: ERROR {r['error'][:130]}")
                    rows.append({"tag": tag, "page": p, "error": r["error"][:200]})
                    continue
                (OUT / f"{tag}.txt").write_text(r["content"] or "", encoding="utf-8")
                body, fns = split_out(r["content"])
                u = r.get("usage") or {}
                row = {
                    "tag": tag, "page": p,
                    "lat": r["latency_s"], "finish": r.get("finish_reason"),
                    "tin": u.get("prompt_tokens"), "tout": u.get("completion_tokens"),
                    "usage_raw": u,
                    "cer_body": round(cer(body, t_body), 4),
                    "cer_fn": round(cer(fns, t_fn), 4) if (fns or t_fn) else None,
                    "len_out": len(r["content"] or ""),
                }
                rows.append(row)
                print(f"{tag}: {row['lat']}s in={row['tin']} out={row['tout']} "
                      f"cer_body={row['cer_body']} cer_fn={row['cer_fn']} finish={row['finish']}")
    (OUT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
