"""MinerU 的 OcrProvider 薄包装：把 stage1_mineru.run_mineru 接入统一契约。

产物仍落 <work_dir>/mineru/（保持既有缓存布局不变）。
"""

from stage1_mineru import run_mineru


class MinerUProvider:
    name = "mineru"

    def parse(self, pdf_path: str, work_dir: str, ocr: bool = True,
              progress=None, **_opts) -> dict:
        return run_mineru(pdf_path, work_dir, ocr=ocr, progress=progress)
