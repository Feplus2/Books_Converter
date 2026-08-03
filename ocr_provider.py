"""Stage 1 Provider 抽象：统一各 OCR/文档解析引擎的产物契约。

不同引擎（MinerU、PaddleOCR-VL…）返回格式各异，但下游 Stage 2/3 只认一套
解析产物。Provider 的职责就是把引擎输出归一化为该契约：

    parse(pdf_path, work_dir, ...) -> {"content_list": [...], "images_dir": str, "markdown": str}

并在 work_dir 下落盘（pipeline 只依赖落盘产物，不依赖返回值）：
    <engine>/{stem}_content_list.json   MinerU 风格块列表：
                                        type/text/text_level/bbox(0-1000)/page_idx/img_path/...
    <engine>/{stem}.md                  引擎直出的 markdown（留档，下游不消费）
    <engine>/images/                    裁剪好的图片文件（img_path 指向文件名）

移植自 Papers_Converter 同名模块。新增引擎：实现 OcrProvider 协议并
register()，或在 _BUILTIN 里登记懒加载入口。
"""

import importlib
import logging
from typing import Callable, Protocol

import config

logger = logging.getLogger(__name__)


class OcrProvider(Protocol):
    """解析引擎协议。实现必须是无状态、可重复实例化的。"""

    name: str  # 注册名，如 "mineru"

    def parse(self, pdf_path: str, work_dir: str, ocr: bool = True,
              progress=None, **opts) -> dict:
        """解析 PDF，产物写入 work_dir/<engine>/，返回
        {"content_list", "images_dir", "markdown"}。

        Args:
            pdf_path: 待解析 PDF 路径
            work_dir: 书籍工作目录（provider 在其下建 <engine>/ 子目录）
            ocr: 是否强制 OCR（扫描版 True，文字版可 False；引擎可忽略）
            progress: 可选回调 progress(detail: str, fraction: float|None)
            **opts: 引擎特定选项（如 model=...）
        """
        ...


def count_pages(pdf_path: str) -> int:
    """获取 PDF 总页数（引擎无关的逻辑共用）。"""
    import fitz  # PyMuPDF，延迟导入
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


# 内置 provider 的懒加载入口：name -> (module, class)
_BUILTIN = {
    "mineru": ("stage1_mineru_provider", "MinerUProvider"),
    "paddleocr": ("stage1_paddleocr", "PaddleOcrProvider"),
}

_FACTORIES: dict[str, Callable[[], OcrProvider]] = {}


def register(name: str, factory: Callable[[], OcrProvider]) -> None:
    """注册第三方/自定义 provider（本地引擎适配器等）。"""
    _FACTORIES[name] = factory


def provider_names() -> list[str]:
    return sorted(set(_BUILTIN) | set(_FACTORIES))


def get_provider(name: str | None = None) -> OcrProvider:
    """按名取 provider 实例；name 为空时用 config.OCR_PROVIDER。"""
    name = name or config.OCR_PROVIDER
    if name in _FACTORIES:
        return _FACTORIES[name]()
    if name in _BUILTIN:
        module_name, class_name = _BUILTIN[name]
        cls = getattr(importlib.import_module(module_name), class_name)
        return cls()
    raise ValueError(
        f"未知 OCR 引擎: {name!r}（可用: {', '.join(provider_names())}）"
    )
