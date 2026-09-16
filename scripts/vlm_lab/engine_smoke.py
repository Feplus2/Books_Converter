"""VLM 引擎实跑冒烟驱动：真模型跑一段页区间，打印契约统计。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.engine_smoke [start end [work_dir]]
"""
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_vlm import VlmProvider  # noqa: E402


def main() -> None:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 46
    work = sys.argv[3] if len(sys.argv) > 3 else r"_regress\vlm-lab\smoke"
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    out = VlmProvider().parse(r"_regress\vlm-lab\pdfs\minfa.pdf", work,
                              workers=4, start_page=start, end_page=end)
    cl = out["content_list"]
    print("blocks:", len(cl), Counter(b["type"] for b in cl))
    meta = json.loads((Path(work) / "vlm" / "metadata.json").read_text(encoding="utf-8"))
    print("meta:", {k: v for k, v in meta.items()
                    if k in ("pages_read", "failed_pages", "toc_entries", "images", "wall_s")})
    imgs = sorted(f.name for f in (Path(work) / "vlm" / "images").glob("*")) \
        if (Path(work) / "vlm" / "images").is_dir() else []
    print("image files:", imgs)


if __name__ == "__main__":
    main()
