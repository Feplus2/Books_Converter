"""T6 成本与并发实测：民法总论 p100-199 连续 100 页，主模型 glm-5.3-flash/low。
- 顺序 100 页：单页 token 实测、延迟 P50/P95、失败率、单书成本外推（官方价 0.8/2.8 元/M）
- 并发探针：4 线程 16 页突发，测 z.ai 限流行为

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t6_cost [--pages 100] [--probe]
"""
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .common import PDFS, PAGES, RUNS
from .pagerender import render_png
from .prompts import TRANSCRIBE
from .vlm_client import chat

DPI = 150
START, DEFAULT_N = 100, 100
PRICE = (0.8, 2.8)  # 元/M tokens（输入/输出），GLM-5.3-Flash 官方价
OUT = RUNS / "t6"
OUT.mkdir(exist_ok=True)


def page_png(p: int) -> bytes:
    f = PAGES / f"minfa_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDFS / "minfa.pdf", p, DPI))
    return f.read_bytes()


def one(p: int, log: str) -> dict:
    r = chat("zai", "glm-5.3-flash", TRANSCRIBE, images=[page_png(p)], reasoning="low", log=log)
    u = r.get("usage") or {}
    return {"page": p, "lat": r["latency_s"], "error": r.get("error", "")[:100],
            "tin": u.get("prompt_tokens") or 0, "tout": u.get("completion_tokens") or 0,
            "finish": r.get("finish_reason")}


def main() -> None:
    n = DEFAULT_N
    probe_only = "--probe" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--pages="):
            n = int(a.split("=")[1])
    log = str(RUNS / "t6.jsonl")

    if not probe_only:
        rows = []
        t0 = time.time()
        for p in range(START, START + n):
            r = one(p, log)
            rows.append(r)
            if r["error"]:
                print(f"p{p}: ERROR {r['error'][:90]}")
        wall = time.time() - t0
        ok = [r for r in rows if not r["error"]]
        lats = sorted(r["lat"] for r in ok)
        tin = sum(r["tin"] for r in ok)
        tout = sum(r["tout"] for r in ok)
        cost = tin / 1e6 * PRICE[0] + tout / 1e6 * PRICE[1]
        n_done = len(ok)
        summary = {
            "pages_ok": n_done, "pages_err": len(rows) - n_done,
            "wall_s": round(wall, 1),
            "lat_p50": statistics.median(lats) if lats else None,
            "lat_p95": lats[int(len(lats) * 0.95)] if lats else None,
            "tin_avg": tin // max(n_done, 1), "tout_avg": tout // max(n_done, 1),
            "cost_100p_yuan": round(cost, 3),
            "cost_per_book_400p_yuan": round(cost / max(n_done, 1) * 400, 3),
            "wall_per_book_400p_min": round(wall / max(n_done, 1) * 400 / 60, 1),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        (OUT / "summary.json").write_text(json.dumps(
            {"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    # 并发探针：4 线程 × 16 页
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda p: one(p, log), range(220, 236)))
    wall = time.time() - t0
    errs = [r for r in res if r["error"]]
    print(f"probe 4-thread 16p: wall={wall:.1f}s errors={len(errs)} "
          f"({[e['error'][:60] for e in errs][:3]})")


if __name__ == "__main__":
    main()
