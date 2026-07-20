"""Deep dive into bbox data — headings are a VISUAL phenomenon."""
import json, sys, re
from pathlib import Path
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding='utf-8')

LIBRARY = Path(r"D:\My_Library")
target = sys.argv[1] if len(sys.argv) > 1 else None

for book_dir in sorted(LIBRARY.iterdir()):
    if not book_dir.is_dir():
        continue
    if target and target not in book_dir.name:
        continue
    for sub in book_dir.iterdir():
        if sub.is_dir():
            cl_files = list((sub / "mineru").glob("*content_list*.json")) if (sub / "mineru").exists() else []
            if not cl_files:
                continue
            path = cl_files[0]
            print(f"\n{'='*70}")
            print(f"Book: {book_dir.name}")
            print(f"{'='*70}")
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Only look at text blocks, skip noise types
            text_blocks = [b for b in data if b.get("type") == "text" and b.get("text", "").strip()]

            print(f"Total text blocks: {len(text_blocks)}")

            # === 1. bbox height clustering (font size proxy) ===
            # bbox = [x_min, y_min, x_max, y_max]
            # height = y_max - y_min = approximate font size * line count
            heights = []
            for b in text_blocks:
                bbox = b.get("bbox", [])
                if len(bbox) >= 4:
                    h = bbox[3] - bbox[1]
                    text = b.get("text", "")
                    if text.strip():
                        # Normalize: height per line
                        lines = max(1, text.count("\n") + 1)
                        heights.append({
                            "height": round(h / lines, 1),
                            "text_level": b.get("text_level", 0),
                            "text": text[:60],
                            "page": b.get("page_idx", "?"),
                            "text_len": len(text),
                        })

            # Cluster by height
            height_values = sorted(set(round(h["height"]) for h in heights))
            print(f"\n=== Font height clusters (rounded) ===")
            height_groups = defaultdict(list)
            for h in heights:
                height_groups[round(h["height"])].append(h)
            
            for h in sorted(height_groups.keys(), reverse=True):
                group = height_groups[h]
                count = len(group)
                if count < 3:
                    continue
                avg_text_level = sum(g["text_level"] for g in group) / count
                avg_text_len = sum(g["text_len"] for g in group) / count
                sample_texts = [g["text"] for g in group[:5]]
                print(f"  height~{h}px: {count:4d} blocks | avg_text_level={avg_text_level:.1f} | avg_len={avg_text_len:.0f}")
                for s in sample_texts:
                    print(f"    → {s}")

            # === 2. Look at actual heading-like blocks ===
            # What if we just ask: which blocks have text_level >= 1?
            print(f"\n=== Blocks with text_level >= 1 ===")
            tl_blocks = [b for b in text_blocks if b.get("text_level", 0) >= 1]
            print(f"Count: {len(tl_blocks)}")
            for b in tl_blocks[:30]:
                text = b.get("text", "")[:60]
                tl = b.get("text_level", 0)
                bbox = b.get("bbox", [])
                h = bbox[3] - bbox[1] if len(bbox) >= 4 else 0
                page = b.get("page_idx", "?")
                print(f"  p={page:>3} lvl={tl} h={h:.0f}px | {text}")

            # === 3. Pattern scan: structural numbering ===
            print(f"\n=== Structural numbering patterns ===")
            patterns = {
                "第X编": r"第[一二三四五六七八九十百]+编",
                "第X章": r"第[一二三四五六七八九十百]+章",
                "第X节": r"第[一二三四五六七八九十百]+节",
                "Part X": r"^Part\s+[IVX\d]+",
                "Chapter X": r"^Chapter\s+\d+",
                "Section X": r"^Section\s+[A-Z\d]",
                "X.Y.Z": r"^\d+\.\d+(\.\d+)",
                "X.Y": r"^\d+\.\d+\s",
                "一、": r"^[一二三四五六七八九十]+、",
                "（一）": r"^（[一二三四五六七八九十]+）",
                "I. II.": r"^[IVX]+\.\s",
                "A. B.": r"^[A-Z]\.\s",
            }
            for name, pattern in patterns.items():
                matches = [b for b in text_blocks if re.search(pattern, b.get("text", ""))]
                if matches:
                    # What text_level do these have?
                    levels = Counter(b.get("text_level", 0) for b in matches)
                    print(f"  {name}: {len(matches)} matches, text_levels={dict(levels)}")
                    for b in matches[:3]:
                        text = b.get("text", "")[:60]
                        tl = b.get("text_level", 0)
                        bbox = b.get("bbox", [])
                        h = bbox[3] - bbox[1] if len(bbox) >= 4 else 0
                        print(f"    lvl={tl} h={h:.0f}px | {text}")

            # === 4. Check for split headings (the user's concern) ===
            print(f"\n=== Split heading detection ===")
            print("Looking for consecutive short blocks on same page...")
            for i in range(len(text_blocks) - 1):
                b1 = text_blocks[i]
                b2 = text_blocks[i + 1]
                t1 = b1.get("text", "").strip()
                t2 = b2.get("text", "").strip()
                p1 = b1.get("page_idx")
                p2 = b2.get("page_idx")
                
                # Both short, same page, both have text_level
                if (p1 == p2 
                    and len(t1) < 15 and len(t2) < 30
                    and b1.get("text_level", 0) >= 1 
                    and b2.get("text_level", 0) >= 1
                    and t1 and t2):
                    # Possible split heading!
                    combined = t1 + " " + t2 if not re.search(r'[\u4e00-\u9fff]', t1) else t1 + t2
                    bbox1 = b1.get("bbox", [])
                    bbox2 = b2.get("bbox", [])
                    # Check if they're vertically adjacent
                    if len(bbox1) >= 4 and len(bbox2) >= 4:
                        gap = abs(bbox2[1] - bbox1[3])
                        if gap < 30:  # close together
                            print(f"  p={p1} lvl={b1.get('text_level')}/{b2.get('text_level')} | \"{t1}\" + \"{t2}\" → \"{combined}\" (gap={gap:.0f}px)")

            break  # Only first matching book

print("\nDone.")
