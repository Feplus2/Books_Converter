"""fitz 页渲染与文本层抽取。"""
import fitz


def n_pages(pdf_path) -> int:
    with fitz.open(pdf_path) as d:
        return d.page_count


def render_png(pdf_path, page_idx: int, dpi: int = 150) -> bytes:
    with fitz.open(pdf_path) as d:
        pix = d[page_idx].get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def text_layer(pdf_path, page_idx: int) -> str:
    with fitz.open(pdf_path) as d:
        return d[page_idx].get_text()
