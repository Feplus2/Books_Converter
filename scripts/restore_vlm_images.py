# -*- coding: utf-8 -*-
"""VLM 书缓存图块恢复工具（2026-09-17 五书重放事故修复）。

事故形态：vlm_state.db 的 raw_json 在图片提取前落库（img_path 只进内存），
任何从 db 的 content_list 重建都会把 image 块降级成 "[插图…]" 文字占位。
本工具用 <stem>.md 里固化的图引用（原跑配对结果：caption + 路径）把
img_path 确定性写回 db 与 content_list.json，零 VLM 调用、零新变量。

配对规则 = _extract_images 原语义：同页 image 块按序 ↔ 同页 md 引用按序；
caption 归一化不等 → 整页跳过（铁律 0：宁可不做，也不错配）。

用法：
  .venv/Scripts/python.exe scripts/restore_vlm_images.py <work_dir> [--apply]
不带 --apply 为 dry-run（只报告）。--apply 后请重跑
  pipeline.py <pdf> -o <输出> --engine vlm --skip-mineru --format epub,md,tex
让 stage2/stage3 走正式路径重建 popo 与产物。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage1_vlm import (  # noqa: E402
    VlmProvider, _drop_minitoc_lines, _norm_key, _toc_region_pages,
)

_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(images/(p(\d{4})_\d+\.png)\)")


def restore(work_dir: Path, apply: bool) -> int:
    vdir = work_dir / "vlm"
    db_path = vdir / "vlm_state.db"
    md_files = list(vdir.glob("*.md"))
    if not db_path.exists() or not md_files:
        print(f"跳过 {work_dir}: 缺 vlm_state.db 或 .md")
        return 1

    conn = sqlite3.connect(str(db_path))
    pages_json: dict[int, dict] = {}
    for idx, raw in conn.execute("SELECT page_idx, raw_json FROM pages"):
        if raw:
            pages_json[int(idx)] = json.loads(raw)
    row = conn.execute("SELECT value FROM book_state WHERE key='toc_entries'").fetchone()
    toc_entries = json.loads(row[0]) if row and row[0] else []

    # md 图引用按页分组（页内保持出现顺序）
    md = md_files[0].read_text(encoding="utf-8")
    refs: dict[int, list[tuple[str, str]]] = {}
    for cap, fname, pstr in _MD_IMG_RE.findall(md):
        refs.setdefault(int(pstr), []).append((cap, f"images/{fname}"))

    n_paired = n_skipped_page = n_orphan_ref = 0
    warnings: list[str] = []
    for p in sorted(pages_json):
        pj = pages_json[p]
        img_blocks = [b for b in pj.get("blocks", []) if b.get("t") == "image"]
        page_refs = refs.get(p, [])
        if not img_blocks and not page_refs:
            continue
        if len(page_refs) > len(img_blocks):
            warnings.append(f"  p{p}: md 引用 {len(page_refs)} > db 图块 "
                            f"{len(img_blocks)}，整页跳过")
            n_skipped_page += 1
            continue
        # caption 逐个校验，不等 → 可能错位，整页跳过
        bad = False
        for j, (cap, _path) in enumerate(page_refs):
            blk_cap = (img_blocks[j].get("caption") or "").strip()
            if blk_cap and _norm_key(blk_cap) != _norm_key(cap):
                warnings.append(f"  p{p}: 第 {j} 图 caption 不符 "
                                f"(db='{blk_cap[:30]}' md='{cap[:30]}')，整页跳过")
                bad = True
                break
        if bad:
            n_skipped_page += 1
            continue
        for j, (_cap, path) in enumerate(page_refs):
            img_blocks[j]["img_path"] = path
            n_paired += 1
    # db 有图块但 md 无引用的页：保持原样（_to_content_list 降级占位，同原跑语义）

    # 孤儿检查：已配对引用外的 md 引用（不应存在，防御）
    n_md_refs = sum(len(v) for v in refs.values())
    n_orphan_ref = n_md_refs - n_paired
    print(f"{work_dir.name}: 页数={len(pages_json)} md引用={n_md_refs} "
          f"配对={n_paired} 跳过页={n_skipped_page} 未配对引用={n_orphan_ref}")
    for w in warnings[:20]:
        print(w)
    if len(warnings) > 20:
        print(f"  …共 {len(warnings)} 条警告")

    if not apply or n_paired == 0:
        if not apply:
            print("  (dry-run，未落盘；加 --apply 执行)")
        return 0

    # 回写 db（此后任何重建自带 img_path）
    for p, pj in pages_json.items():
        conn.execute(
            "INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?,"
            "datetime('now','localtime'))",
            (p, "ok", json.dumps(pj, ensure_ascii=False),
             len(pj.get("blocks", [])), len(pj.get("footnotes", [])),
             int(bool(pj.get("has_image"))), None, None))
    conn.commit()

    # 重建 content_list（与正跑同函数同顺序：minitoc 丢弃 → 契约转换）
    toc_pages = _toc_region_pages(pages_json)
    n_dropped = _drop_minitoc_lines(pages_json, toc_pages, toc_entries)
    content_list = VlmProvider._to_content_list(None, pages_json, toc_entries, toc_pages)
    cl_path = vdir / f"{md_files[0].stem}_content_list.json"
    cl_path.write_text(json.dumps(content_list, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    n_img = sum(1 for b in content_list if b.get("type") == "image")
    print(f"  已回写 db + 重建 content_list（图块={n_img}，minitoc 丢弃 {n_dropped}）"
          f" → {cl_path.name}")
    print("  下一步：pipeline.py --skip-mineru（不 --skip-deepseek）重跑正式重建")
    conn.close()
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    apply = "--apply" in sys.argv
    rc = 0
    for a in args:
        rc |= restore(Path(a), apply)
    return rc


if __name__ == "__main__":
    sys.exit(main())
