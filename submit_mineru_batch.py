"""
批量提交 MinerU Stage 1 (OCR only).
Python 自己发现 PDF文件，绕过bash中文路径编码问题。
"""
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# 清理代理
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

from stage1_mineru import run_mineru, save_mineru_metadata

LIBRARY = Path(r"D:\My_Library")

# 自动发现: 每个子目录下的第一个 PDF
books = []
for book_dir in sorted(LIBRARY.iterdir()):
    if not book_dir.is_dir():
        continue
    pdfs = list(book_dir.glob("*.pdf"))
    if pdfs:
        books.append(pdfs[0])

print(f"发现 {len(books)} 本书:")
for i, pdf in enumerate(books, 1):
    print(f"  {i}. {pdf.name} ({pdf.parent.name})")
print()

for i, pdf in enumerate(books, 1):
    book_name = pdf.stem
    work_dir = pdf.parent / book_name
    mineru_out = work_dir / "mineru"
    md_files = list(mineru_out.glob("*.md")) if mineru_out.exists() else []

    if md_files:
        print(f"[{i}/{len(books)}] {book_name} — MinerU 缓存已存在，跳过")
        continue

    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{i}/{len(books)}] {book_name} — 开始 MinerU Stage 1...")
    t0 = time.time()
    try:
        info = run_mineru(
            str(pdf), str(work_dir), ocr=True,
            progress=lambda detail: print(f"    {detail}", flush=True),
        )
        save_mineru_metadata(str(work_dir), info)
        elapsed = time.time() - t0
        print(f"    ✓ 完成 ({elapsed:.0f}s, {len(info['content_list'])} blocks)")
    except Exception as e:
        print(f"    ✗ 失败: {e}")

print("\n全部完成。")
