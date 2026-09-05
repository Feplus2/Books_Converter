# 02 结构系统：锚点与层级语义（本仓库的复杂度核心）

> 改这一页所述的任何规则前，先读 FIXLOG 对应病例。所有守卫的失败方向
> 必须是"不动作"。

## 两个真值来源

| 来源 | 性质 | 风险 |
|---|---|---|
| 印刷目录页 → LLM 提取的 `toc_entries` | 文本+层级+印刷页码，绝对真值 | 采样截断（`_PAGE_CHARS`，现 4000）、LLM 编造（无目录页时）、伪造指纹 |
| PDF outline/书签（born-digital 免费） | 扫描页码精确，优先级更高 | 可能是'标题／页码'假目录（`_sanitize_pdf_toc`）、泛名 'Chapter N'（病例 022）、ISBN 噪声条目（纯数字长串拒收） |

伪造目录硬兜底：两个目录页识别器都没找到目录页 → 丢弃 LLM 目录；
≥80% 条目页码与标题块扫描页全等（抄了标题列表页码）→ 丢弃。

## 锚点匹配规则（`_match_anchor`，stage2_common）

归一化（`_normalize_title`：剥脚注上标/装饰前缀/去 $ 与空白/casefold/
弯直引统一）后按序：

1. **精确命中**（含 `_PROBLEM_PREFIX_RE` 剥离键：'Problem 3.1. X' → 'X'，
   词表限定+必须带数字；病例 024）；
2. 块是锚点前缀（**块归一化键须 ≥3 字符或含 CJK**——单字母 'A' 曾前缀
   命中 'Acknowledgments'）；
3. 块是锚点尾部（分隔页模式，取最短）；
4. 锚点是块的前缀（目录截断，限长锚防 '1.1' 误配 '1.1.2'）；
5. 块是锚点子串（**须覆盖锚长 ≥40%**——单词块 'Depression' 曾子串命中
   分区条目锁成幻影 L1）；
6. 模糊兜底（有界编辑距离；**超限必须拒绝**——`_edit_distance_le` 超限
   返回 limit+1，旧的 best_dist=3 曾照单全收；系列守卫：块=锚点+数字/
   字母后缀是系列另一项，不得命中）。

`_build_anchors` 拒收退化条目（有效字符 <3 且无 CJK，如截断残渣 'VI.'）。

## finish_structure 的执行顺序（stage2_common.py，顺序即语义）

```
重页丢弃 → 轻量兜底（metadata/前后页/toc_entries）
→ outline 清洗+泛名层级重建（normalize_generic_outline_levels）+采用
→ 目录页码修复 + 目录页识别（降格集只取 ≤25 页：章首 mini-TOC 防护，病例 023）
→ 伪造目录判定 → front_matter 目录边界修正
→ _calibrate_levels（锚定校正）：
     目录页标题降格 → 锚点救援（噪声类型 header/footer/page_number 永不晋升；
     纯文本块过位置闸门：已锚定标题估计 印刷页→扫描页 偏移，±8 页；
     引擎已标标题的块豁免闸门——罗马页码/附录另起页码 regime 下偏移不成立）
     → 图注几何过滤 → 形状栈
→ _anchor_generic_outline（泛名书签位置锚定：书签页=扫描页真值；
     候选 title/header/text，页顶优先 bbox y1；纯文本晋升 ≤45% 页高闸；
     拒绝 'N.M' 本章小节；记 _pos_num）
→ _rescue_by_page（页码救援/合成：偏移众数 <3 票放弃；
     合成块只插稀疏页；节级只晋升不合成）
→ _dedup_anchored_titles（锚点身份查重：同条目多块留一；
     目录序三明治一致性择优——唯一命中锚点作骨架，候选须落在骨架相邻
     锚点的目录序区间内；与译文无关，病例 021）
→ _veto_junk_titles（无锚垃圾否决：单字母/单 CJK 字/署名行/单个英文词；
     复发 ≥3 的相同短标题豁免——'Summary'/'Exercises' 章末小节；锚定豁免）
→ _sink_unanchored_plain（无编号无锚标题下沉到最近锚定下一级——
     "目录里没有的标题不许浮到章级"；字号阶梯只许再下沉不许上浮）
→ _global_level_pass（LLM 全局一致性定级；锚定块锁死跳过）
→ 写 popo_blocks.json → popo.build_tree（tree 仅供 QC）
```

## 不变量（invariants）

- **锚定即锁死**：`_anchored=True` 的块，下沉/全局定级/否决器全部跳过；
  锚点是比几何、比 LLM 票都强的证据。
- **目录里没有的不许浮上来**：无编号无锚标题一律深于所属锚定章。
- **查重看身份不看文本**：同一条目的多个标题块 = 同一章（译文措辞差异
  不影响）；stage3 的相邻近似章合并是兜底不是主力。
- **噪声永远不是标题**：header/footer/page_number/aside_text/discarded
  不参与晋升（唯一例外：泛名书签位置锚定的页顶候选——书签页是章首页，
  页顶 header 即章题，病例 022 追记）。
- **内容永不丢**：降格/否决/查重只改 type/level，文本块始终作为正文段落
  渲染。

## Stage 3 切章与 nav

`_spine_from_toc`（用词形定 partition/spine：part/编/罗马数字 'I. …'/
'Section IV' → 分区；chapter/第X章/'12. …' → 章。**多级编号 '1.1'/'5.5.1'
有 (?!\d) 前瞻，永不触发章词形**，病例 023）→ 失败回退启发式
（spine=最小标题层级，顶层多为分区词形时再深一级）。

线性切章：`level ≤ partition` → divider（编）；`level ≤ spine` → chapter；
`level == spine+1` → nav 子标题；更深只出 h 标签。**章级判重只许同 kind
合并**——分区页与首章仅一词重合也会被吞（'STRUCTURE' vs
'Crystal structure'，病例 022 追记）。

`_body_range`：front_matter 之后 ~ back_matter 之前；`toc_source=outline`
时泛名条目页码回收正文起点（LLM 前页分类波动的防护）。章号显示：
`_pos_num` 块补 'N. ' 前缀（display-only）。

`epub3_pages` 关闭：管线不产 pagebreak 锚点，开启时 ebooklib 会把脚注回链
收进 page-list（fnref_* 进目录的病例 019）。

## 已知边界（不要"顺手修"）

- 字号聚类（`_height_ladder_map`）只许下沉：bbox 高度是检测区域高，
  在扫描件上无分离度（Feeling Great 实测真章标题比值 0.12–6.11）。
- LLM 不定性（前页边界、目录条目数、首票层级）是固有噪声轴；守卫要
  对波动鲁棒，不要对单次输出过拟合。
- 超长多行章题（150+ 字符跨页拆块）锚不上属既有局限（Born a Crime
  ch10/12/14）。
