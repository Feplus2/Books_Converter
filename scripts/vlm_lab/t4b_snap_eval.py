"""T4b 光栅重裁 A/B：GLM 粗框 vs GLM+snap（vs MinerU 裁剪真值，doubao 成绩引用 T4）。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.t4b_snap_eval
"""
import io
import json
import re

from PIL import Image

from .common import PAGES, RUNS, TRUTH
from .t4_image_bbox import IMAGE_PAGES, PROMPT, iou, parse_bboxes, truth_bboxes
from .vlm_client import chat

OUT = RUNS / "t4b"
OUT.mkdir(exist_ok=True)


def main() -> None:
    import sys
    sys.path.insert(0, str(PAGES.parents[2]))
    from raster_snap import merge_overlaps, snap_box_cc

    log = RUNS / "t4b.jsonl"
    rows = []
    for p in IMAGE_PAGES:
        truth = truth_bboxes(p)
        if not truth:
            continue
        img = Image.open(io.BytesIO((PAGES / f"minfa_p{p:03d}_150.png").read_bytes()))
        r = chat("zai", "glm-5.3-flash", PROMPT,
                 images=[(PAGES / f"minfa_p{p:03d}_150.png").read_bytes()],
                 reasoning="low", max_tokens=4096, log=str(log))
        if "error" in r:
            print(f"p{p}: ERROR {r['error'][:100]}")
            continue
        rough = [b for b in parse_bboxes(r["content"])
                 if all(0 <= v <= 1200 for v in b) and b[2] - b[0] > 20 and b[3] - b[1] > 20]
        rough = merge_overlaps(rough)
        snapped = [snap_box_cc(img, b) for b in rough]

        def best_ious(preds):
            return [max((iou(pb, tb) for pb in preds), default=0.0) for tb in truth]

        bi_r, bi_s = best_ious(rough), best_ious(snapped)
        rows.append({"page": p, "rough": bi_r, "snapped": bi_s, "n_pred": len(rough)})
        print(f"p{p}: pred={len(rough)} truth={len(truth)} "
              f"rough_iou={[round(v,2) for v in bi_r]} snapped={[round(v,2) for v in bi_s]}")
    (OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    flat_r = [v for r in rows for v in r["rough"]]
    flat_s = [v for r in rows for v in r["snapped"]]
    for name, flat in (("GLM 粗框", flat_r), ("GLM+重裁", flat_s)):
        print(f"{name}: recall@0.5={sum(v >= 0.5 for v in flat)}/{len(flat)} "
              f"IoU≥0.8={sum(v >= 0.8 for v in flat) / max(len(flat), 1):.1%} "
              f"meanIoU={sum(flat) / max(len(flat), 1):.3f}")


if __name__ == "__main__":
    main()
