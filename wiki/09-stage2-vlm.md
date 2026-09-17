# 09 VLM 专用 Stage 2（stage2_vlm）— 设计稿 v1（用户已批准，2026-09-17）

> 背景（用户裁定原话）："规则 Stage2 这套玩意过于复杂，曾经误伤了 VLM 正确
> 标注好的页眉，把它'反向救援'回正文……不应该让陈旧的规则妨碍强大的新
> 技术。即便需要维护两套管线，我也觉得是值得的——新管线 stage2 中，那套
> 复杂混乱的救援规则或许能够得到大量简化。"
>
> 切法（已对齐）：**共享一份函数库，分拆两个编排器**。规则引擎继续走
> `stage2_hybrid`（它处理的就是烂输入，一行不动）；VLM 走新的 `stage2_vlm`
> （薄编排，信任优先 + 校验导向）。`stage2_common` 的底层函数（标题归一、
> 锚点匹配器、形状栈、降格守卫）两编排器共用——它们 engine-agnostic，
> 且 037/038/042 等 VLM 时代修复都在这层，复制等于把未来 bug 翻倍。

## 1. 为什么拆（证据）

- `stage2_hybrid` 对每本书无差别跑四项 DeepSeek 标注（contd/标题/图文/表格，
  stage2_hybrid.py:140-147）。VLM 书 = 花钱让只读文本的弱模型改亲眼看过
  版面的强模型的判断，还白付延迟。
- VLM 的目录先验（vlm_state.db `toc_entries`，结构化、带 level/印刷页码）
  此前根本没进 Stage 2；hybrid 仍用 OCR 时代的"找目录页+解析条目"检测器，
  病例 037 我们把 VLM 输出掰弯成 MinerU 形态去喂检测器——旧规则拖新技术
  的活标本。
- 救援体系（页码救援/幻影合成/轻量兜底标注）的全部预设是"输入缺页漏标、
  结构靠猜"。VLM 输入自带 text_level/running_head/脚注配对/目录先验，
  救援规则对它只有副作用面（"反向救援"误伤即此）。

## 2. 裁撤 / 保留 / 新增清单

| 阶段（finish_structure 现状） | VLM 版处置 | 理由 |
|---|---|---|
| DeepSeek 四项分块标注（contd/title/image/table） | **裁** | stage1 已给出 text_level/图文配对/pipe 表/脚注配对 |
| `_light_metadata_pass`（metadata+前后页分类） | **保留** | 单次调用成本低；metadata/keep 标记是前后页导出的输入（后期可换 VLM 原生，挂账） |
| LLM 目录提取 + 规则目录兜底 + 伪造目录判定 + `_repair_toc_pages` | **裁** | VLM 目录先验直接作锚（T2 实测召回/页码/层级 100%） |
| `_drop_duplicate_pages` | 保留 | 廉价的源 PDF 防重 |
| `_calibrate_levels`（锚定校正） | **保留** | 模型有方差（单字错 0.1–0.3%），锚定仍是核心；复用 stage2_common 匹配器 |
| `_anchor_generic_outline` | 裁 | outline 已弃用（病例 028），VLM 路径无此输入 |
| `_rescue_by_page`（页码救援/回补合成标题） | **裁（核心）** | "反向救援"源头。VLM 条目锚不上 → 报告（QC/交叉校验），绝不合成 |
| `_dedup_anchored_titles` / `_veto_junk_titles` | 保留 | 只降格不晋升的守卫，VLM 同样需要（页眉/重块） |
| `_sink_unanchored_plain` | 保留 | 无编号小标题的层级约束，规则版无模型成本 |
| `_global_level_pass`（LLM 全局定级） | **裁，换规则版** | text_level 提示 + 形状栈一致性收口；不再为定级调 LLM |
| `_anchor_frontmatter_lexicon` | 保留 | 前后页词表是免费真值（前言/序/版权页） |
| `_fix_front_matter_toc` / `popo.build_tree` | 保留 | 与引擎无关 |
| **新增**：目录先验 ↔ 正文标题交叉校验 | 新增 | 伊豆挂账的正主：同一标题在目录先验与正文块文本差异超阈值 → 记 `structure["cross_check"]` 报告（先只报告不自动重读，见 §4 二期） |
| **新增**：printed_page 对齐校验 | 新增（轻量） | stage1 每页带 printed_page，可校验目录印刷页码 ↔ 扫描页偏移一致性，异常进报告 |

## 3. stage2_vlm 编排（落地产物契约与 hybrid 完全一致）

```
analyze_structure_vlm(content_list, book_name, work_dir,
                      vlm_state_db, progress) -> structure.json 同构
  1. content_list → 标注 blocks（本地映射，零 LLM）：
     text_level>0 → title/level；page_footnote/header/page_number 类型透传；
     contd 规则版：页尾无终止标点 + 下页首块非标题形状 → 拼接（干净文本上
     规则远比 OCR 文本可靠）；image 块已带 img_path/caption 透传；
     跨页表格复用 popo table_merge util
  2. 目录先验：读 vlm_state.db 的 toc_entries → 直接作为锚点表输入
     （跳过一切目录检测/兜底/伪造判定）
  3. _drop_duplicate_pages → _light_metadata_pass（保留，唯一 LLM 调用点）
  4. _calibrate_levels（锚定校正，复用匹配器+形状栈）
  5. 守卫三件套（dedup/veto/sink，全部只降格不晋升）
  6. 规则版层级一致性（形状栈收口，替代 _global_level_pass）
  7. _anchor_frontmatter_lexicon → popo.build_tree
  8. 交叉校验 + printed_page 校验 → structure["cross_check"] 报告
产物：popo_blocks.json + structure.json（字段与 hybrid 输出完全同构，
      engine="vlm-hybrid"）→ stage3/stage3_export/qc_book 零感知
```

失败方向约定（铁律 0）：stage2_vlm 里**不存在任何"合成"动作**——
不合成标题、不合成页码、不编造锚点；任何校验不通过的去向是"报告"，
不是"修补"。

## 4. 二期（本次不做，挂账）

- 交叉校验升级为行动：差异超阈值时**重读该页**（目录先验与正文标题
  不一致是模型单点误读的最强探针——伊豆'供養/牧場'案例）
- metadata/front-back 分类 VLM 原生化（去掉唯一 LLM 调用点）
- 目录先验条目级置信度（同条目多目录页重复出现=高置信）
- FG 挂账 3 项（FIXLOG 043 续）：divider 长标题被 calibrate 救援的 64
  长度帽拦下（该帽生于 FG CliffsNotes 案，方向正确不松）、em-dash
  归一化（—/–/- 统一进 `_normalize_title`）候选、索引区判定的其他
  形态书回归（QFT 无索引、minfa 无索引已验证）

## 5. 测试与回归（AGENTS.md 强制链）

- 新测试 `tests/test_stage2_vlm.py`：contd 规则、text_level 映射、目录先验
  直接作锚（无目录检测器调用）、守卫只降格、交叉校验报告形状。
- 存量单测全绿（stage2_common 共享层行为不变）。
- **七本 VLM 存量书对照回归**（复用 stage1 缓存，只重跑 stage2/3，零转写
  成本）：高数（新 PDF，页序已修正）/民法总论/Feeling Great/Born a Crime/
  QFT/必须保卫社会/汉语语义学。判据：锚定数不降低、幻影=0、QC 红=0、
  nav 亲读抽查（章首归属、层级形状）。
- 收尾：FIXLOG 登记（编号顺延）、wiki/01 架构图更新、AGENTS.md 验证链补
  test_stage2_vlm.py。

## 6. 里程碑状态

- 2026-09-17 设计稿 v1 拍板；同日实现（stage2_vlm.py ~540 行 +
  pipeline 分流 + stage3_epub 门禁追认 vlm-hybrid）+ tests/test_stage2_vlm.py
  9 例落地，子代理 minfa 冒烟 anchor 234/234。
- **七本对照回归完成**（结果与自迭代细节见 FIXLOG 043 续）：
  must_defend 🟢、qft 🟢、born 🟢、hanyu 🟡（固有黄）、gaoshu 🟡（固有黄，
  新 PDF 全量重跑）、minfa 全中；FG 41/43 挂账 3 项（见 §4）。
  回归逼出并已修：索引污染切除（_cut_index_tail + _demote_index_region_titles）、
  编号句点归一、书眉判定不复发化（title_freq ≥3）。
- 遗留挂账：db 先验自身方差（民法 四、/五、 锚 L5 vs 兄弟 L4）；
  `_rule_table_merge` 缺真书 exercising（与 FIXLOG 042 表格深度合并验证）。
