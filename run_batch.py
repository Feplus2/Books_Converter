#!/usr/bin/env python3
"""run_batch.py — 批量跑书器：串行转换 + 结构体检汇总。

用法:
    python run_batch.py --engine paddleocr book1.pdf [book2.pdf ...]
    python run_batch.py --engine mineru "D:\\My_Library\\*\\*.pdf"

每本书独立子进程跑 pipeline.py（崩溃不连坐），日志存 _batch_logs/<书名>.log，
完成后逐本 qc_book 体检并输出汇总表。有红牌退出码 = 1。
"""

import argparse
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qc_book import check_book  # noqa: E402

LOG_DIR = Path(__file__).parent / "_batch_logs"


def run_one(pdf: Path, engine: str) -> dict:
    name = pdf.stem.rstrip(" .") or pdf.stem
    work_dir = pdf.parent / name
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"

    cmd = [sys.executable, str(Path(__file__).parent / "pipeline.py"),
           str(pdf), "--engine", engine, "--headless"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT,
                              env=env, cwd=Path(__file__).parent)
    elapsed = time.time() - t0

    result = {"pdf": pdf, "name": name, "ok": proc.returncode == 0,
              "elapsed": elapsed, "log": log_path}
    if work_dir.exists():
        try:
            result["qc"] = check_book(str(work_dir))
        except Exception as e:
            result["qc"] = {"verdict": "ERROR", "red": [f"体检异常: {e}"],
                            "yellow": [], "stats": {}}
    else:
        result["qc"] = {"verdict": "RED", "red": ["无产物目录（转换失败）"],
                        "yellow": [], "stats": {}}
    return result


def main():
    ap = argparse.ArgumentParser(description="批量跑书 + 体检汇总")
    ap.add_argument("pdfs", nargs="+", help="PDF 路径（支持 glob）")
    ap.add_argument("--engine", default="mineru",
                    choices=["mineru", "paddleocr"])
    args = ap.parse_args()

    pdfs = []
    for pat in args.pdfs:
        matched = glob.glob(pat)
        pdfs.extend(Path(p) for p in (matched or [pat]))
    pdfs = [p for p in pdfs if p.exists()]
    if not pdfs:
        print("没有找到任何 PDF")
        sys.exit(2)

    print(f"批量转换 {len(pdfs)} 本书，引擎: {args.engine}\n")
    results = []
    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.stem[:50]} ...", flush=True)
        r = run_one(pdf, args.engine)
        results.append(r)
        icon = "✓" if r["ok"] else "✗"
        print(f"      {icon} {r['elapsed']:.0f}s → {r['qc']['verdict']}",
              flush=True)

    print("\n" + "=" * 70)
    print("体 检 总 表")
    print("=" * 70)
    n_red = 0
    for r in results:
        qc = r["qc"]
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(
            qc["verdict"], "❓")
        if qc["verdict"] == "RED":
            n_red += 1
        s = qc.get("stats", {})
        info = (f"pages={s.get('pages')} anchor={s.get('anchor_hit')} "
                f"synth={s.get('synth_titles')} miss={s.get('missing_real')}")
        print(f"{icon} {r['name'][:44]:<46} {info}")
        for msg in qc.get("red", []):
            print(f"     🔴 {msg}")
        for msg in qc.get("yellow", []):
            print(f"     🟡 {msg}")
    print(f"\n红牌 {n_red}/{len(results)}，日志在 {LOG_DIR}")
    sys.exit(1 if n_red else 0)


if __name__ == "__main__":
    main()
