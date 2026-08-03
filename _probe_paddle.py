"""临时探针：确认 PaddleOCR-VL API 返回结构（bbox 坐标空间 / 页面尺寸字段 / 表格形态）。
用完可删。不打印 token。
"""
import json
import sys
import time
from pathlib import Path

import fitz
import requests

SRC_PDF = Path(r"F:/MyProjects/Papers_Converter/output/teoh2010flame/source.pdf")
PAPERS_ENV = Path(r"F:/MyProjects/Papers_Converter/.env")
PAGES = 3


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env(PAPERS_ENV)
    api_url = env.get("PADDLEOCR_API_URL",
                      "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs")
    token = env["PADDLEOCR_TOKEN"]

    # 取前 N 页做成小 PDF
    tmp = Path("_probe_pages.pdf")
    doc = fitz.open(SRC_PDF)
    sub = fitz.open()
    sub.insert_pdf(doc, from_page=0, to_page=PAGES - 1)
    sub.save(tmp)
    print(f"probe pdf: {tmp} ({tmp.stat().st_size/1024:.0f} KB, {PAGES} pages)")
    print(f"src page0 rect: {doc[0].rect}")
    doc.close()

    headers = {"Authorization": f"bearer {token}"}
    data = {
        "model": "PaddleOCR-VL-1.6",
        "optionalPayload": json.dumps({
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }),
    }
    with open(tmp, "rb") as f:
        resp = requests.post(api_url, headers=headers, data=data,
                             files={"file": f}, timeout=180)
    print("submit:", resp.status_code)
    if resp.status_code != 200:
        print(resp.text[:500])
        return
    job_id = resp.json()["data"]["jobId"]
    print("jobId:", job_id)

    json_url = None
    t0 = time.time()
    while time.time() - t0 < 900:
        jr = requests.get(f"{api_url}/{job_id}", headers=headers, timeout=60)
        d = jr.json()["data"]
        if d["state"] == "done":
            json_url = d["resultUrl"]["jsonUrl"]
            break
        if d["state"] == "failed":
            print("FAILED:", d.get("errorMsg"))
            return
        print("  state:", d["state"])
        time.sleep(5)
    print(f"done in {time.time()-t0:.0f}s")

    text = requests.get(json_url, timeout=180).text
    Path("_probe_result.jsonl").write_text(text, encoding="utf-8")
    pages = []
    for line in text.strip().split("\n"):
        if line.strip():
            pages.extend(json.loads(line)["result"]["layoutParsingResults"])
    print(f"pages: {len(pages)}")

    p0 = pages[0]
    print("\n== page-level keys ==", sorted(p0.keys()))
    pr = p0.get("prunedResult", {})
    print("== prunedResult keys ==", sorted(pr.keys()))
    for k in ("width", "height", "page_size", "pageSize", "input_path",
              "page_index", "page_count"):
        if k in p0:
            print(f"page[{k}] =", p0[k])
        if k in pr:
            print(f"prunedResult[{k}] =", pr[k])

    blocks = pr.get("parsing_res_list", [])
    print(f"\n== page0 blocks: {len(blocks)} ==")
    labels = {}
    for b in blocks:
        labels[b.get("block_label")] = labels.get(b.get("block_label"), 0) + 1
    print("labels:", labels)
    for b in blocks[:6]:
        print(f"  label={b.get('block_label')!r} bbox={b.get('block_bbox')} "
              f"keys={sorted(b.keys())}")
        c = (b.get("block_content") or "")[:80].replace("\n", "\\n")
        print(f"    content: {c!r}")

    # 全部页里找 table / formula 块看形态
    for pi, page in enumerate(pages):
        for b in page.get("prunedResult", {}).get("parsing_res_list", []):
            if b.get("block_label") in ("table", "formula", "display_formula",
                                        "interline_formula"):
                c = (b.get("block_content") or "")
                print(f"\n== {b['block_label']} (page {pi}) bbox={b.get('block_bbox')} ==")
                print(c[:300])
                break

    # markdown.images 键样例
    imgs = p0.get("markdown", {}).get("images") or {}
    print("\n== markdown.images keys (page0) ==")
    for k in list(imgs)[:5]:
        print(" ", k)

    # bbox 数值范围（判断坐标空间）
    maxv = 0
    for page in pages:
        for b in page.get("prunedResult", {}).get("parsing_res_list", []):
            bb = b.get("block_bbox") or []
            if bb:
                maxv = max(maxv, max(bb))
    print(f"\nmax bbox coord across {PAGES} pages: {maxv}")


if __name__ == "__main__":
    main()
