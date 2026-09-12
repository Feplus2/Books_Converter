"""光栅重裁：把模型的粗 bbox 吸附到页图的真实内容边界（空白沟槽）。

纯 PIL、零依赖、确定性。书籍场景的图 = 大墨迹块，与正文/图注之间有空白
沟槽；模型框（glm 级：区域对、边缘松）外扩后，在行/列投影上找沟槽收边。

失败方向=不动作：吸附结果退化（面积异常/越界/太小）一律回退原始粗框。
"""
import logging
from PIL import Image

logger = logging.getLogger(__name__)

_AREA_LIMIT = (0.3, 3.0)  # 吸附后面积 / 粗框面积 的合法区间
_CC_WORK_W = 600         # 连通域重裁工作宽度
_CC_MIN_AREA = 0.0005    # 连通域最小面积（占整页比例，滤掉噪声点/字母）


def _binarize(img: Image.Image, max_w: int = _CC_WORK_W):
    """工作分辨率灰度自适应二值化（ink=1）+ 墨迹膨胀（连通断笔/细线）。"""
    from PIL import ImageFilter
    g = img.convert("L")
    if g.size[0] > max_w:
        r = max_w / g.size[0]
        g = g.resize((max_w, max(1, int(g.size[1] * r))), Image.BILINEAR)
    hist = g.histogram()
    tot = sum(hist)
    cum = 0
    thr = 200
    for v, n in enumerate(hist):
        cum += n
        if cum >= tot * 0.6:
            thr = v
            break
    thr = max(80, min(thr, 200))
    bw = g.point(lambda x: 1 if x < thr else 0)
    bw = bw.filter(ImageFilter.MaxFilter(3))   # 膨胀：连通细线、吃掉小缝
    return bw


def _components(bw: Image.Image, min_area: int) -> list[tuple]:
    """4-连通域 BFS。返回 [(x1,y1,x2,y2,area), ...]（>= min_area）。"""
    from collections import deque
    w, h = bw.size
    px = bw.load()
    seen = bytearray(w * h)
    comps = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if px[x, y] and not seen[idx]:
                q = deque([(x, y)])
                seen[idx] = 1
                x1 = x2 = x
                y1 = y2 = y
                area = 0
                while q:
                    cx, cy = q.popleft()
                    area += 1
                    x1 = min(x1, cx)
                    x2 = max(x2, cx)
                    y1 = min(y1, cy)
                    y2 = max(y2, cy)
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < w and 0 <= ny < h:
                            ni = ny * w + nx
                            if px[nx, ny] and not seen[ni]:
                                seen[ni] = 1
                                q.append((nx, ny))
                if area >= min_area:
                    comps.append((x1, y1, x2, y2, area))
    return comps


def snap_box_cc(img: Image.Image, bbox: list[float]) -> list[float]:
    """连通域光栅重裁：以粗框中心为种子，收集相连的墨迹连通域，取联合包围盒。

    正文行/页眉/图注与图形本体不相连 → 自动排除；图形被粗框切断时，
    连通域自然延伸到完整内容。失败方向=回退粗框（不动作）。
    """
    W, H = img.size
    bw = _binarize(img)
    w, h = bw.size
    sx, sy = w / W, h / H
    x1, y1, x2, y2 = [bbox[0] / 1000 * w, bbox[1] / 1000 * h,
                      bbox[2] / 1000 * w, bbox[3] / 1000 * h]
    if x2 - x1 < 4 or y2 - y1 < 4:
        return list(bbox)
    comps = _components(bw, int(_CC_MIN_AREA * w * h))
    if not comps:
        return list(bbox)

    def overlap(c, rx1, ry1, rx2, ry2, tol=2):
        return not (c[2] < rx1 - tol or c[0] > rx2 + tol or
                    c[3] < ry1 - tol or c[1] > ry2 + tol)

    # 种子：与粗框中心带（50%）相交的连通域；无则与整框相交的
    cx1, cx2 = x1 + (x2 - x1) * 0.25, x2 - (x2 - x1) * 0.25
    cy1, cy2 = y1 + (y2 - y1) * 0.25, y2 - (y2 - y1) * 0.25
    sel = [c for c in comps if overlap(c, cx1, cy1, cx2, cy2)] or \
          [c for c in comps if overlap(c, x1, y1, x2, y2)]
    if not sel:
        return list(bbox)
    # 生长：反复并入与当前联合包围盒相交的连通域
    ux1 = min(c[0] for c in sel)
    uy1 = min(c[1] for c in sel)
    ux2 = max(c[2] for c in sel)
    uy2 = max(c[3] for c in sel)
    grew = True
    while grew:
        grew = False
        for c in comps:
            if overlap(c, ux1, uy1, ux2, uy2):
                if c[0] < ux1 or c[1] < uy1 or c[2] > ux2 or c[3] > uy2:
                    ux1, uy1 = min(ux1, c[0]), min(uy1, c[1])
                    ux2, uy2 = max(ux2, c[2]), max(uy2, c[3])
                    grew = True
    # 1% 边距 + 回映射
    pad_x, pad_y = w * 0.01, h * 0.01
    out = [max(0, ux1 - pad_x) / w * 1000, max(0, uy1 - pad_y) / h * 1000,
           min(w, ux2 + pad_x) / w * 1000, min(h, uy2 + pad_y) / h * 1000]
    # 退化守卫
    a0 = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    a1 = (out[2] - out[0]) * (out[3] - out[1])
    if not (_AREA_LIMIT[0] <= a1 / max(a0, 1) <= _AREA_LIMIT[1]) \
            or (out[2] - out[0]) < 30 or (out[3] - out[1]) < 30:
        return list(bbox)
    return [round(v, 1) for v in out]


def merge_overlaps(boxes: list[list[float]], iou_thr: float = 0.3,
                   contain_thr: float = 0.8) -> list[list[float]]:
    """多图页：吸附后重叠（IoU>0.3 或互相包含>0.8）的框合并为同一图。"""
    def area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    def iou(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        return inter / max(area(a) + area(b) - inter, 1e-9), inter / max(min(area(a), area(b)), 1e-9)

    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                v, cont = iou(boxes[i], boxes[j])
                if v > iou_thr or cont > contain_thr:
                    boxes[i] = [min(boxes[i][0], boxes[j][0]), min(boxes[i][1], boxes[j][1]),
                                max(boxes[i][2], boxes[j][2]), max(boxes[i][3], boxes[j][3])]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes
