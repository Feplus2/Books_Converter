#!/usr/bin/env python3
"""批量提交 MinerU (Stage 1 only)，用于预热新书。"""

import subprocess
import sys
from pathlib import Path

PYTHON = Path(__file__).parent / ".venv" / "Scripts" / "python.exe"
PIPELINE = Path(__file__).parent / "pipeline.py"

BOOKS = [
    r"D:\My_Library\高等数学\高等数学·上册 第七版 (同济大学数学系) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
    r"D:\My_Library\德意志意识形态\The German Ideology (Great Books in Philosophy) (Karl Marx, Friedrich Engels) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
]

for pdf in BOOKS:
    print(f"\n{'='*60}")
    print(f"  提交: {Path(pdf).name}")
    print(f"{'='*60}")
    result = subprocess.run(
        [str(PYTHON), str(PIPELINE), pdf, "--skip-deepseek"],
        env={**__import__("os").environ, "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": ""},
    )
    if result.returncode != 0:
        print(f"  !! 失败 (exit {result.returncode})")
    else:
        print(f"  OK")

print("\n全部完成。")
