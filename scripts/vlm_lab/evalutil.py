"""评测工具：输出解析、CER 计算、差异窗口提取（供亲读抽查）。"""
import difflib
import re

from .common import cer, edit_distance, norm


def split_out(content: str) -> tuple[str, str]:
    """解析 <body>/<footnotes> 区块；无标记时全文当 body。"""
    if not content:
        return "", ""
    body = re.search(r"<body>(.*?)</body>", content, re.S)
    fns = re.search(r"<footnotes>(.*?)</footnotes>", content, re.S)
    b = body.group(1) if body else content
    f = fns.group(1) if fns else ""
    return b.replace("[图]", "").replace("[image]", ""), f


def page_scores(out_body: str, out_fn: str, truth_body: str, truth_fn: str) -> dict:
    nb, nt = norm(out_body), norm(truth_body)
    e_body = edit_distance(nb, nt)
    r = {
        "cer_body": round(e_body / max(len(nt), 1), 4),
        "edits_body": e_body,
        "chars_truth": len(nt),
        "chars_out": len(nb),
    }
    if out_fn.strip() or truth_fn.strip():
        nf, ntf = norm(out_fn), norm(truth_fn)
        e_fn = edit_distance(nf, ntf)
        r.update(cer_fn=round(e_fn / max(len(ntf), 1), 4), edits_fn=e_fn, chars_fn_truth=len(ntf))
    else:
        r.update(cer_fn=None, edits_fn=0, chars_fn_truth=0)
    return r


def direction_counts(out: str, truth: str) -> dict:
    """差异方向分解：VLM 多出的字符数（insert/replace 的 out 侧）vs 缺失的字符数
    （delete/replace 的 truth 侧）。用于区分"VLM 漏字"与"真值本身缺块"。"""
    import difflib
    a, b = norm(out), norm(truth)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    extra = missing = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        extra += i2 - i1
        missing += j2 - j1
    return {"extra_chars": extra, "missing_chars": missing}


def diff_windows(out: str, truth: str, n: int = 3, ctx: int = 30) -> list[dict]:
    """归一化后找前 n 个差异窗口，供人工判断谁对谁错。"""
    a, b = norm(out), norm(truth)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    wins = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        wins.append({
            "kind": tag,
            "out": a[max(0, i1 - ctx): i2 + ctx],
            "truth": b[max(0, j1 - ctx): j2 + ctx],
        })
        if len(wins) >= n:
            break
    return wins
