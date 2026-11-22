"""Generate a Chinese scan and exercise the live DeepSeek OCR → Hybrid → EPUB path.

Run from the repository: python scripts/check_deepseek_ocr.py NEW_OUTPUT_DIR
Uses the configured OCR and post-processing credentials.
"""
import sys
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pymupdf
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def create_scan(path):
    pages = [
        [(24, 85, "溪边观测手记"), (20, 160, "目录"),
         (16, 220, "第一章　雨后的水位　　　2"), (16, 270, "第二章　清晨的记录　　　3")],
        [(22, 70, "第一章　雨后的水位"), (15, 120, "雨停以后，我们沿着溪岸查看水尺。①"),
         (14, 330, "图一　溪水深度随时间的变化"), (15, 405, "记录员决定等到第二天早晨再"),
         (11, 500, "① 水尺固定在石桥东侧，读数单位为厘米。")],
        [(15, 55, "补记一次，让两次测量可以互相参照。"), (22, 130, "第二章　清晨的记录"),
         (15, 180, "溪面恢复平静，水位下降了两厘米。")],
    ]
    with pymupdf.open() as source, pymupdf.open() as scan:
        for index, lines in enumerate(pages):
            page = source.new_page(width=420, height=560)
            for size, y, text in lines:
                page.insert_text((32, y), text, fontname="china-s", fontsize=size)
            if index == 1:
                page.draw_rect((70, 180, 335, 290), color=(.2, .3, .45))
                page.draw_polyline([(85, 265), (155, 242), (220, 223), (312, 202)],
                                   color=(.1, .4, .8), width=3)
            image = page.get_pixmap(dpi=144)
            out = scan.new_page(width=420, height=560)
            out.insert_image(out.rect, stream=image.tobytes("png"))
        scan.save(path)


def main():
    dest = Path(sys.argv[1]).resolve()
    dest.mkdir(parents=True, exist_ok=False)
    pdf = dest / "溪边观测手记.pdf"
    create_scan(pdf)
    subprocess.run([sys.executable, str(ROOT / "pipeline.py"), str(pdf),
                    "--headless", "--engine", "deepseek-ocr", "-o", str(dest)], check=True)
    epub, = dest.rglob("*.epub")
    with ZipFile(epub) as book:
        nav = BeautifulSoup(book.read("EPUB/nav.xhtml"), "html.parser")
        targets = {a.get_text(strip=True).replace(" ", ""): a["href"] for a in nav.select("a[href]")}
        for title in ("第一章雨后的水位", "第二章清晨的记录"):
            assert title in targets, targets
        first = "EPUB/" + targets["第一章雨后的水位"].split("#")[0]
        html = BeautifulSoup(book.read(first), "html.parser")
        assert "早晨再补记一次" in "".join(html.get_text().split())
        assert "溪水深度" in html.get_text()
        assert any("EPUB/" + img["src"] in book.namelist() for img in html.select("img[src]"))
        links = [(a.get("id"), a["href"][1:]) for a in html.select('a[href^="#"]')]
        assert len(links) >= 2 and all(html.find(id=target) for _, target in links), links
    print(epub)


if __name__ == "__main__":
    main()
