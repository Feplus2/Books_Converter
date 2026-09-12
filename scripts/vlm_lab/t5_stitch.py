"""T5 跨页上下文拼接：10 组连续页，page N 单独转写 vs 带上页尾部上下文转写。
指标：
- 续写污染率（输出开头复述上页尾部 >30 字符）——必须≈0
- 一致性 CER（alone vs with_ctx 的 body 差异，应≈0）
- 边界完整性（with_ctx 输出前 50 字符与 alone 相同前缀比例）

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t5_stitch
"""
import json

from .common import PDFS, PAGES, RUNS, TRUTH, cer, norm
from .evalutil import split_out
from .pagerender import render_png
from .prompts import TRANSCRIBE, TRANSCRIBE_WITH_CONTEXT
from .vlm_client import chat

DPI = 150
PAIRS = [(151, 152), (152, 153), (178, 179), (215, 216), (237, 238),
         (255, 256), (280, 281), (295, 296), (320, 321), (377, 378)]
PRIMARY = ("zai", "glm-5.3-flash", "low")
SECONDARY = ("deepseek", "deepseek-flash", "off")
OUT = RUNS / "t5"
OUT.mkdir(exist_ok=True)


def page_png(p: int) -> bytes:
    f = PAGES / f"minfa_p{p:03d}_{DPI}.png"
    if not f.exists():
        f.write_bytes(render_png(PDFS / "minfa.pdf", p, DPI))
    return f.read_bytes()


def prefix_overlap(a: str, tail: str) -> int:
    """a 的开头与 tail 的结尾的最长公共长度（归一化后）。"""
    a, tail = norm(a), norm(tail)
    best = 0
    for k in range(min(len(a), len(tail)), 19, -1):
        if a[:k] == tail[-k:]:
            best = k
            break
    return best


def run_pair(prov, model, lv, p_prev, p_cur, log):
    r1 = chat(prov, model, TRANSCRIBE, images=[page_png(p_prev)], reasoning=lv, log=log)
    if "error" in r1:
        return {"error": f"prev: {r1['error'][:120]}"}
    body_prev, _ = split_out(r1["content"])
    tail = body_prev.strip()[-300:]

    r2 = chat(prov, model, TRANSCRIBE, images=[page_png(p_cur)], reasoning=lv, log=log)
    r3 = chat(prov, model, TRANSCRIBE_WITH_CONTEXT.format(prev_tail=tail),
              images=[page_png(p_cur)], reasoning=lv, log=log)
    for nm, r in (("alone", r2), ("ctx", r3)):
        if "error" in r:
            return {"error": f"{nm}: {r['error'][:120]}"}
    body_a, _ = split_out(r2["content"])
    body_c, _ = split_out(r3["content"])
    (OUT / f"{prov}_{model.replace('/', '-')}_p{p_cur}_alone.txt").write_text(r2["content"] or "", encoding="utf-8")
    (OUT / f"{prov}_{model.replace('/', '-')}_p{p_cur}_ctx.txt").write_text(r3["content"] or "", encoding="utf-8")
    poll = prefix_overlap(body_c, tail)
    na, nc = norm(body_a), norm(body_c)
    pref50 = 0
    for k in range(min(50, len(na), len(nc)), 0, -1):
        if na[:k] == nc[:k]:
            pref50 = k
            break
    return {
        "page": p_cur, "prev": p_prev,
        "tail_chars": len(tail),
        "pollution_chars": poll,
        "consistency_cer": round(cer(body_a, body_c), 4),
        "prefix_same": pref50,
        "lat_alone": r2["latency_s"], "lat_ctx": r3["latency_s"],
    }


def main() -> None:
    log = str(RUNS / "t5.jsonl")
    rows = []
    for prov, model, lv in (PRIMARY, SECONDARY):
        for a, b in PAIRS:
            r = run_pair(prov, model, lv, a, b, log)
            r.update(model=f"{prov}/{model}")
            rows.append(r)
            if "error" in r:
                print(f"{prov}/{model} p{b}: ERROR {r['error'][:110]}")
            else:
                print(f"{prov}/{model} p{b}: pollution={r['pollution_chars']} "
                      f"consist_cer={r['consistency_cer']} prefix_same={r['prefix_same']}")
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [r for r in rows if "error" not in r]
    if ok:
        print(f"\npollution>30: {sum(1 for r in ok if r['pollution_chars'] > 30)}/{len(ok)}  "
              f"consist_cer avg: {sum(r['consistency_cer'] for r in ok)/len(ok):.4f}")


if __name__ == "__main__":
    main()
