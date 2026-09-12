"""资产准备：暂存 PDF（书库只读，复制进 _regress）+ 从 MinerU 缓存提取逐页真值 + 样本页选取。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.prepare_assets
"""
import json
import re
import shutil

from .common import PDFS, TRUTH
from .pagerender import n_pages, text_layer

BOOKS = {
    "minfa": {
        "pdf": r"D:\My_Library\民法总论\民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "content_list": r"D:\My_Library\民法总论\民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk)\mineru\民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk)_content_list.json",
        "popo_blocks": r"D:\My_Library\民法总论\民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk)\popo_blocks.json",
    },
    "qft": {
        "pdf": r"_regress\QFT\A Modern Introduction to Quantum Field Theory (Michele Maggiore) (Z-Library).pdf",
        "content_list": r"_regress\QFT\A Modern Introduction to Quantum Field Theory (Michele Maggiore) (Z-Library)\mineru\A Modern Introduction to Quantum Field Theory (Michele Maggiore) (Z-Library)_content_list.json",
        "popo_blocks": r"_regress\QFT\A Modern Introduction to Quantum Field Theory (Michele Maggiore) (Z-Library)\popo_blocks.json",
    },
}

_TOC_LINE_RE = re.compile(r"[\.·…•\-—–\s]{2,}\d{1,4}$|\d{1,4}$")
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[编章]")


def stage_pdf(key: str) -> None:
    b = BOOKS[key]
    dst = PDFS / f"{key}.pdf"
    if not dst.exists():
        shutil.copyfile(b["pdf"], dst)
    print(f"[{key}] staged {dst.name} pages={n_pages(dst)} text_layer_p100={len(text_layer(dst, 100))}chars")


def extract_truth(key: str) -> None:
    b = BOOKS[key]
    cl = json.load(open(b["content_list"], encoding="utf-8"))
    pages: dict[int, dict] = {}
    for blk in cl:
        p = blk.get("page_idx")
        if p is None:
            continue
        tp = blk.get("type", "text")
        t = (blk.get("text") or "").strip()
        d = pages.setdefault(p, {"text": [], "footnotes": [], "headers": [], "images": [], "tables": [], "other": []})
        if tp == "text":
            d["text"].append(t)
        elif tp == "page_footnote":
            d["footnotes"].append(t)
        elif tp in ("header", "footer", "page_number", "aside_text"):
            d["headers"].append(t)
        elif tp == "image":
            d["images"].append(blk.get("bbox"))
        elif tp == "table":
            d["tables"].append(t[:80])
        else:
            d["other"].append(f"[{tp}] {t[:60]}")
    out = TRUTH / key
    out.mkdir(exist_ok=True)
    for p, d in pages.items():
        (out / f"p{p:03d}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{key}] truth pages={len(pages)} -> {out}")


def select_samples(key: str) -> dict:
    b = BOOKS[key]
    cl = json.load(open(b["content_list"], encoding="utf-8"))
    by_page: dict[int, list] = {}
    for blk in cl:
        by_page.setdefault(blk.get("page_idx", -1), []).append(blk)

    toc_pages, chapter_pages, fn_pages, image_pages, plain_pages = [], [], [], [], []
    # popo_blocks 拿锚定标题页（章首真值）
    popo = json.load(open(b["popo_blocks"], encoding="utf-8"))
    anchored = {}
    for blk in popo:
        if blk.get("_anchored") and blk.get("level") is not None and blk.get("level") <= 2:
            anchored.setdefault(blk["page"] - 1, []).append((blk.get("level"), (blk.get("content") or "")[:30]))

    for p in sorted(by_page):
        blocks = by_page[p]
        texts = [ (blk.get("text") or "").strip() for blk in blocks if blk.get("type") == "text" ]
        n_fn = sum(1 for blk in blocks if blk.get("type") == "page_footnote")
        n_img = sum(1 for blk in blocks if blk.get("type") == "image")
        if p < 40:
            tocish = sum(1 for t in texts if 4 < len(t) < 60 and _TOC_LINE_RE.search(t))
            if tocish >= 4:
                toc_pages.append(p)
        if any(_CHAPTER_RE.match(t) and len(t) < 40 for t in texts) or p in anchored:
            chapter_pages.append(p)
        if n_fn:
            fn_pages.append((p, n_fn))
        if n_img:
            image_pages.append(p)
        if texts and not n_fn and not n_img and 60 < p < 500:
            plain_pages.append(p)

    fn_pages.sort(key=lambda x: -x[1])
    even = plain_pages[:: max(1, len(plain_pages) // 10)]
    samples = {
        "toc": toc_pages[:6],
        "chapter": chapter_pages[:8],
        "footnote_top": [p for p, _ in fn_pages[:12]],
        "image": image_pages[1:9],          # p0 是封面，跳过
        "plain": even[:8],
        "anchored_l12": {str(k): v for k, v in sorted(anchored.items())},
    }
    (TRUTH / f"{key}_samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{key}] samples: toc={samples['toc']} chapter={samples['chapter']}")
    print(f"  footnote_top={samples['footnote_top']} image={samples['image']} plain={samples['plain']}")
    return samples


if __name__ == "__main__":
    for k in BOOKS:
        stage_pdf(k)
        extract_truth(k)
        select_samples(k)
