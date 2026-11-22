"""DeepSeek hosted OCR: recognize PDF batches and normalize grounded page output."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

import pymupdf as fitz
import requests

import config
from stage1_layout import convert_layout_blocks

_ENDPOINT = "https://api.deepseek.com/v1/tools/ocr"
_MARKER = re.compile(r"<｜ref｜>([^<]+)<｜/ref｜>\s*<｜box｜>([^<]+)<｜/box｜>")
_LABELS = {"title": "doc_title", "sub_title": "paragraph_title",
           "image_caption": "image_caption", "table_caption": "table_title"}
_NOTE = re.compile(r"^([①-⑳])\s*")


def _write(path, data):
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _book_layout(raws):
    """Use printed order for a left-page-number TOC and isolated chapter openers."""
    body = [r for r in raws if r["label"] not in {"header", "footer", "page_number"}]
    if any(r["content"] in {"目录", "目錄"} for r in body):
        entries = []
        for r in body:
            m = re.fullmatch(r"(\d{1,4})\s+(.+)", r["content"])
            if m and r["label"] == "text":
                entries.append((r, int(m[1]), m[2]))
        numbers = [n for _, n, _ in entries]
        if len(numbers) >= 3 and all(a < b for a, b in zip(numbers, numbers[1:])):
            for r, n, title in entries:
                r["content"] = f"{title}　{n}"
        return
    if len(body) not in (2, 3) or any(r["label"] != "text" for r in body):
        return
    marker, title = body[:2]
    if (re.fullmatch(r"第[一二三四五六七八九十百千零〇两\d]+章", marker["content"])
            and 1 < len(title["content"]) <= 32
            and title["bbox"][1] - marker["bbox"][3] >= 80
            and title["bbox"][3] - title["bbox"][1] >= marker["bbox"][3] - marker["bbox"][1]):
        title["label"] = "paragraph_title"
        title["content"] = f"{marker['content']} {title['content']}"
        title["bbox"] = [min(marker["bbox"][0], title["bbox"][0]), marker["bbox"][1],
                         max(marker["bbox"][2], title["bbox"][2]), title["bbox"][3]]
        raws.remove(marker)


def _page_blocks(page, text, images):
    matches = list(_MARKER.finditer(text))
    number = page.number + 1
    if text[:matches[0].start() if matches else None].strip():
        raise ValueError(f"第 {number} 页含未定位的 OCR 文本")
    raws = []
    anchors = set()
    for i, match in enumerate(matches):
        content = text[match.end():matches[i+1].start() if i+1 < len(matches) else None].strip()
        if "<｜ref｜>" in content or "<｜box｜>" in content:
            raise ValueError(f"第 {number} 页版面标记不完整")
        content = re.sub(r"</?center>", "", content).strip()
        boxes = json.loads(match[2])
        if not boxes or not all(len(b) == 4 and 0 <= b[0] < b[2] <= 1000
                                and 0 <= b[1] < b[3] <= 1000 for b in boxes):
            raise ValueError(f"第 {number} 页坐标无效")
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)]
        label = _LABELS.get(match[1], match[1])
        if label == "text":
            note = _NOTE.match(content)
            if note and bbox[1] >= 800 and note[1] in anchors:
                label = "page_footnote"
            else:
                anchors.update(re.findall(r"(?<=\S)[①-⑳]", content))
        raws.append(dict(label=label, content=content, bbox=bbox, index=i))

    _book_layout(raws)

    def crop(raw):
        # A page's displayed dimensions include its rotation, as does the OCR image.
        box = raw["bbox"]
        rect = fitz.Rect(*(v * (page.rect.width if i % 2 == 0 else page.rect.height)
                           / 1000 for i, v in enumerate(box)))
        name = f"p{number}_{raw['index']}.png"
        page.get_pixmap(clip=rect, dpi=220).save(images / name)
        return name

    return convert_layout_blocks(raws, page.number, images, None, save_image=crop)


def _recognize(payload, key, first, count, cache, filename="pages.pdf", mime="application/pdf"):
    if cache.exists():
        result = json.loads(cache.read_text(encoding="utf-8"))
    else:
        if not key:
            raise ValueError("请配置 DEEPSEEK_OCR_API_KEY")
        response = requests.post(_ENDPOINT, headers={"Authorization": f"Bearer {key}"},
                                 files={"file": (filename, payload, mime)},
                                 data={"purpose": "rich_layout_recognition"},
                                 timeout=(15, 195), allow_redirects=False)
        response.raise_for_status()
        result = response.json()
    if result.get("error"):
        raise RuntimeError(f"OCR 第 {first+1} 页起: {result['error'].get('message')}")
    pages = result.get("pages", [])
    if len(pages) != count or result.get("page_count") != count:
        raise ValueError(f"OCR 第 {first+1} 页起返回页数不符")
    for i, page in enumerate(pages):
        if (page.get("index") != i or page.get("finish_reason") != "stop"
                or not isinstance(page.get("text"), str)):
            raise ValueError(f"OCR 第 {first+i+1} 页返回不完整")
    if not cache.exists():
        _write(cache, json.dumps(result, ensure_ascii=False))
    return pages


def _page_text(page, text, cache_dir, key):
    if text.strip():
        return text
    preview = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    if min(preview.samples) >= 245:
        return ""
    logging.info("DeepSeek OCR 第 %s 页有可见内容，复查单页图像", page.number + 1)
    cache = cache_dir / f"page-{page.number}-jpeg.json"
    jpeg = page.get_pixmap(dpi=190).tobytes("jpeg", jpg_quality=90)
    result = _recognize(jpeg, key, page.number, 1, cache, "page.jpg", "image/jpeg")
    if result[0]["text"].strip():
        return result[0]["text"]
    logging.warning("DeepSeek OCR 第 %s 页两次识别为空，保留原页图像", page.number + 1)
    return "<｜ref｜>image<｜/ref｜><｜box｜>[[0,0,1000,1000]]<｜/box｜>"


class DeepSeekOcrProvider:
    name = "deepseek-ocr"

    def parse(self, pdf_path, work_dir, ocr=True, progress=None, **opts):
        key = os.getenv("DEEPSEEK_OCR_API_KEY")
        if not key and config.DEEPSEEK_BASE_URL.rstrip("/") in (
                "https://api.deepseek.com", "https://api.deepseek.com/v1"):
            key = config.DEEPSEEK_API_KEY
        out = Path(work_dir) / self.name
        images = out / "images"
        images.mkdir(parents=True, exist_ok=True)
        with open(pdf_path, "rb") as src:
            h = hashlib.sha256()
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                h.update(chunk)
            digest = h.hexdigest()
        cache_dir = out / "batches" / digest
        cache_dir.mkdir(parents=True, exist_ok=True)
        blocks, texts = [], []
        with fitz.open(pdf_path) as doc:
            first = 0
            while first < len(doc):
                count = min(24, len(doc) - first)
                while True:
                    with fitz.open() as part:
                        part.insert_pdf(doc, from_page=first, to_page=first+count-1)
                        payload = part.tobytes(garbage=3, deflate=True)
                    if len(payload) <= 100 * 1024**2:
                        break
                    if count == 1:
                        raise ValueError(f"第 {first+1} 页超过 100 MiB")
                    count //= 2
                cache = cache_dir / f"{first}-{count}.json"
                pages = _recognize(payload, key, first, count, cache)
                for i, p in enumerate(pages):
                    page = doc[first + i]
                    text = _page_text(page, p["text"], cache_dir, key)
                    page_blocks = _page_blocks(page, text, images)
                    blocks.extend(page_blocks)
                    texts.extend(f"![插图](images/{b['img_path']})" if b.get("img_path")
                                 else b.get("text", b.get("table_body", "")) for b in page_blocks)
                first += count
                if progress:
                    progress(f"DeepSeek OCR {first}/{len(doc)}", first / len(doc))
        stem = Path(pdf_path).stem
        markdown = "\n\n".join(texts)
        _write(out / f"{stem}.md", markdown)
        _write(out / f"{stem}_content_list.json", json.dumps(blocks, ensure_ascii=False, indent=2))
        return {"content_list": blocks, "images_dir": str(images), "markdown": markdown}
