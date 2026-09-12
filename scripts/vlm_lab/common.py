"""共享：路径 / 配置 / 文本归一化 / 编辑距离。数据一律落 _regress/vlm-lab/（gitignored）。"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "_regress" / "vlm-lab"
PDFS, PAGES, RUNS, TRUTH = LAB / "pdfs", LAB / "pages", LAB / "runs", LAB / "truth"
for _d in (PDFS, PAGES, RUNS, TRUTH):
    _d.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))


def load_providers() -> dict:
    return json.loads((LAB / "providers.json").read_text(encoding="utf-8"))


def norm(s: str) -> str:
    """转写对比归一化：NFKC + 去全部空白（换行/空格差异不惩罚）。"""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s)


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[lb]


def cer(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a and not b:
        return 0.0
    return edit_distance(a, b) / max(len(a), len(b), 1)
