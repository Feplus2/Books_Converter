# FIXLOG — 兜底策略病例登记

每本"翻车"书一条病例，格式固定：

> **书目 / 引擎** | 现象 → 根因链 → 修补点（文件：函数）→ 回归测试 → 状态

阅读指南：结构判断的修补只落在三层——
**适配器层**（stage1_*：把引擎脾气翻译成契约，只动文本形态）、
**契约信任层**（stage2_common：凡 LLM 输出皆首票，真值来自几何与投票）、
**系统层**（路径/打包/分片，与内容无关）。
每条守卫的失败方向必须是"不动作"，不是"乱动作"。

---

## 病例 001｜必须保卫社会 / PaddleOCR — 幻影章节

- **现象**：EPUB 被切成 20+ 章，章名错乱重复（'one 7 JANUARY 1976' 出现在错误页）。
- **根因链**：目录页码被排成独立 aside_text 数字块（与条目分离）→
  LLM 提取 toc_entries 配对不上，瞎编等差页码（真实 1/23/43… 编成 1/17/33…）→
  `_rescue_by_page` 偏移投票全乱 → 按错误偏移在正文合成 8 个幻影标题块。
- **修补**（stage2_common.py）：
  - `_repair_toc_pages`（新）：条目块与数字块按 bbox y 坐标同行重配真实页码
    （dy≤25 千分位贪心，支持罗马数字→置空，因其与正文偏移 regime 不同）；
  - `_rescue_by_page`：偏移众数 <3 票直接放弃救援（票分散 = 页码不可信）；
    已满足判定加"完整键精确命中即满足"（强证据不看位置）；
  - `_build_anchors`：剥尾页码保留完整形态（'1976' 不是页码是年份）；
    恰好两级且高层级 ≤2 个无编号条目 → 收敛到多数层级（防 LLM 拔高前言）；
  - `_calibrate_levels`：目录页标题降格（toc_pages 由 repair 识别：
    ≥3 条目命中 + ≥3 数字块）；同页同文标题去重（含锚点富化后的重复）；
  - `_normalize_title`：加 casefold（'FOREWORD' ↔ 'Foreword: …'）。
- **回归**：tests/test_stage2_toc.py — repair_* / rescue_* / anchor_* /
  calibrate_* 共 10 例；本书重跑树 = one–eleven 平铺、零幻影。
- **状态**：已修复并验证。

## 病例 002｜必须保卫社会 / 系统层 — 文件名尾空格

- **现象**：`--skip-mineru` 重跑时 FileNotFoundError: 工作目录找不到。
- **根因**：PDF 文件名 '...(David Macey) .pdf' 词干以空格结尾，Windows 建目录
  时静默剥离，后续按原名 iterdir 失败。
- **修补**：pipeline.py / app.py 统一 `stem.rstrip(" .")`。
- **回归**：本书重跑通过。**状态**：已修复。

## 病例 003｜民法总论 / PaddleOCR — 轻量兜底截断 + 转义污染 + 目录多形态

- **现象**：首跑只剩 15 个平章，编层级丢失；二跑 53 章过度切分（目录条目 +
  列表项混进树）。
- **根因链**（四个独立问题）：
  1. 目录 ~200 条，`_light_metadata_pass` 单次响应超 max_tokens 截断 →
     toc_entries 全丢 → 0 锚点；
  2. 固定 200 页分片 → 3 片排队 48 分钟；
  3. PaddleOCR 输出 markdown 转义（'1\. 要件'、214 处 '\]'）→ 编号形状检测
     失效 → 列表项被投成 L1；
  4. 简目（独立条目）+ 详目（点线页码 blob 块）并存，目录页识别单一形态
     覆盖不了；且初版按行识别误杀章首页（章+本章节标题，形似小目录）。
- **修补**：
  - stage2_common：轻量兜底失败拆两个紧凑重试（`_LIGHT_META_PROMPT` +
    `_LIGHT_TOC_PROMPT` 数组格式只采样书首）；
  - stage1_paddleocr：`_chunk_pages` 按平均页体积自适应（≤45MB/≤1000 页，
    本书单任务 709s，提速 4 倍）；`_MD_ESCAPE_RE` 数学区外解转义；
  - stage2_common：`_detect_toc_pages_by_entries`（新）：条目行命中 ≥3 +
    （数字行 ≥3 或占比 ≥50%）+ **印刷页码跨度 >30**（章首页跨度≈0 不误杀）+
    邻页扩展（长目录残余页）。
- **回归**：tests 新增 parse_toc_array / unescape / detect_toc_pages_* 6 例；
  终验 **21 单元（6 编 15 章）与 MinerU 基准完全一致**。
- **状态**：已修复并验证。残留 cosmetic：'Default Title' 空章（popo 占位）、
  第六编 重复一章（页眉被 _PART_HEADER 捞回，与真章扉页相邻同名）。

---

## 已知边界（不是 bug，暂不接）

- LLM 标题投票有 ±1 轮波动（同一本书两跑标题数差几个）——锚点+形状栈
  吸收大部分，残余为散文小标题被投成 L1（内容无损）。
- LLM 提取的目录**层级**仍可能错（两级目录收敛只兜"拔高前言"一种形态）。
- 页码与条目同行但 y 错位 >25 千分位的目录版式，重配会漏。

## 当前回归资产

- 单元测试：`tests/test_stage2_toc.py`（23 例，每例对应真实病例场景，
  运行 `python tests/test_stage2_toc.py`）
- 结构体检表：`qc_book.py <work_dir>...`（内容完整=红：非空块缺失/
  目录条目未锚上/幻影合成块；结构瑕疵=黄：重复标题/空章/降级 metadata；
  自动配套 content_list 与 popo_blocks 的引擎版本）
- 基准书目（结构真值）：
  - 民法总论（561p，七层深目录）：**21 单元 = 6 编 15 章，874+ 页脚注**
    （MinerU / PaddleOCR 双引擎须一致）
  - 必须保卫社会（328p，英文，页码分离目录）：one–eleven 平铺、0 幻影
  - teoh2010flame（21p 论文，跨页大表）：5 章、11 HTML 表、上下标残留 0

## 病例 004｜刘擎西方现代思想讲义 / PaddleOCR — 后页节标题三连丢

- **现象**：'答学友问'、'参考文献'、'人名索引' 三个后页节标题未锚上。
- **根因链**（三种独立的匹配缺口）：
  1. '答学友问' 正文块是 '答学友问1'（差 1 字），模糊匹配门槛 len≥6 把它挡在门外；
  2. '人名索引 $^{①}$' 的上标脚注标记进了归一化键；
  3. '参考文献' 的正文标题只以 header 形态出现（PaddleOCR 把章节首页的
     标题行当页眉），popo/convert.py 丢弃全部非编名 header。
- **修补**：`_match_anchor` 模糊门槛 len≥4；`_normalize_title` 剥 `$^{...}$`
  与法式装饰前缀 '— X. —'；popo/convert.py 页眉每种文本首次出现保留为
  text（交锚点晋升裁决，编名仍直接捞回为标题）。
- **顺带**：同页缩写标题去重（'21 | 阿伦特Ⅱ' vs 完整章名，去标点+前导
  编号后前缀判定，同级弃缩写）——解决章占位页的"空章"问题。
- **回归**：anchor 52/55 → **55/55**，黄牌及以下。
- **状态**：已修复并验证。

## 病例 005｜Naissance de la clinique / PaddleOCR — 装饰前缀幻影

- **现象**：页码救援在 P194 合成 '— X. — La crise des flèvres' 幻影标题。
- **根因**：目录条目带法式装饰前缀 '— X. —'，且 OCR 把 'flèvres' 识成
  'fièvres'（l/i 一字之差）——真实块 'la crise des fièvres' 距离 >2 锚不上。
- **修补**：`_normalize_title` 剥装饰前缀后距离=1，模糊命中，幻影消失。
- **回归**：anchor 13/13、synth=0。**状态**：已修复并验证。

## 病例 006｜Condensed Matter Physics / PaddleOCR — 详目与截断条目

- **现象**：详目页（blob 形态，单页只覆盖一章小节）未被目录页识别；
  4 个小节标题未锚上。
- **根因**：
  1. 目录页识别的印刷页跨度门槛 30 太严——详目单页跨度仅 ~22；
     章首页跨度≈0 才是误判源 → 门槛降至 5（章首页依旧安全）；
  2. 轻量兜底紧凑重试的响应里末尾条目被 LLM 截断（'9.2.1 Kau'），
     前缀规则只覆盖"块是锚点前缀"，不覆盖反向。
- **修补**：跨度门槛 30→5；`_match_anchor` 加"锚点是块的前缀"
  （限锚点 ≥6 字符，防 '1.1' 误配 '1.1.2'）。
- **回归**：未锚 4 条全部锚上（168/172 → 172/172 待终验）。
- **已知残留**（黄牌，内容无损）：运行头=章名的书会有跨页重复标题与
  空章（缩写去重只管同页）；'CHAPITRE VIII' 多页重复章名同类。
- **状态**：已修复，终验中。

## 病例 007｜伊豆の踊子（竖排日语）/ PaddleOCR — 能力边界

- **现象**：竖排全书。体检仅黄牌：metadata 完整（川端康成/偕成社/ja），
  34/34 标题锚定，5 个 'image' 空章（插页被投成标题）。
- **结论**：PaddleOCR-VL 对竖排日语的支持超出预期，正文可转；
  'image' 空章与假名连浊误差属引擎层边界，结构层不硬修。
- **状态**：记录在案，不接。

## 病例 008｜刘擎 + 全语料 / 结构层级深化（几何证据 + 下沉约束）

- **背景**：用户指出两类"能用但不好用"——CMP 图注被误判为标题造成层级紊乱；
  刘擎篇内无编号小标题（'思想内在于现实'）吸附到讲次同级。
- **机制修复**（证据优先级：锚点 > 几何 > LLM 首票）：
  1. **图注/表注位置过滤**（`_calibrate_levels`）：与图块 x 重叠、垂直紧贴
     （图注在图下/表注在表上 ≤3% 页高）、字号不明显大于正文的标题候选
     降回正文；锚得上的绝不动。
  2. **无编号无锚标题下沉**（`_sink_unanchored_plain`）：无编号标题若真是
     编/章级目录里一定有它，锚不上就必须严格深于所属锚定章。
- **连锁暴露的四个纠缠问题**：
  1. 斜杠页码（'xxx / 060'）未剥 → 目录页识别失效、目录整份混进树
     （24 个空章）→ `_TRAIL_PAGE_RE` 统一剥点线/空格/斜杠/破折号；
  2. 系列守卫：'答学友问1..12' 全部模糊命中父锚点 '答学友问' 逃过下沉
     → 块=锚点+数字/字母后缀时禁止模糊命中；
  3. 下沉必须在**页码救援之后**（系列块依赖救援先锚定首项）；
  4. 模糊上限不对称：LLM 笔误让条目比正文长（'PRÉSPACE' vs 'PRÉFACE'）
     → 上限按两者较长者定（<8 容 1，否则容 2）。
- **QC 同步**：page_fuzzy 位置晋升视为满足（系列块的合法锚定方式）。
- **回归**：刘擎空章 24→1、55/55 锚定、答学友问系列正确嵌套；
  单元测试 32/32。
- **状态**：已修复并验证。25 45  23

## 病例 009｜全语料 / 目录页识别升级 + 运行头收敛 + 孤儿编号

- **目录页识别第三判据**（用户提议的"页码特征"落地）：点线引导行
  （'xxx …… 60'、'xxx ..... 153'）几乎只出现在目录/索引页，≥3 行且
  页码跨度 >5 即判目录页——与引擎和条目文本都无关，是最本质的判据。
  罗马数字页码在 `_repair_toc_pages` 已处理（置空不参与偏移）。
- **运行头收敛**：同一标题文本在更早页面已出现、本块位于页首（y2≤8%）
  → 页眉重复降回正文（锚点视为已被首次出现消费）。CMP 跨页重复标题
  的主要来源。
- **孤儿编号先救后罚**（`_fix_orphan_series`）：某编号家族全书无 '1'
  且系列 ≥3 起跳 → 几乎必是列表项误判（'4.' 孤挂层级顶）；先在附近
  ±3 页找 '1' 晋升救回，找不到才把整个系列降回正文。2 起跳保守不罚。
- **运行头规则的一个教训**：初版"锚定块不动"使规则完全失效（运行头
  与章名同文，也能锚上）——锚点必须视为被首次出现消费。
- **回归**：单元测试 35/35；全语料终验见当轮汇报。
- **状态**：已修复并验证。

### 病例 009 终验结果（2026-08-03）

- 必须保卫社会：🟢 全绿；刘擎 55/55（空章 1）；民法总论 165/165；
  伊豆 26/26；Naissance 13/13。
- CMP 重复标题 31→0（运行头收敛生效），但体检新红 4 条未锚——排查结论
  **非本轮回归**：'10 Crystal dynamics' 等 2 条在 content_list 中根本不存在
  （PaddleOCR 漏识别章头，正文内容完好；稠密章首页按设计不合成标题）；
  '13 Electrons in the periodic potential' 同；'A.6 Elements of statistical
  mechanics' 是印刷目录与正文标题的真实措辞差异（目录多 'Elements of'，
  编辑距离 9 超出模糊上限）。三条均属**引擎层边界**，结构层不接。
  本轮 toc_entries 191 条（LLM 抽取波动，上轮 172 条不含这 4 条）。

## 已知边界（更新）

- 章头被 OCR 整块漏识别且首页稠密：内容完好但标题不进目录
  （稠密页不合成是有意取舍——插错位置比缺标题更糟）。
- 印刷目录与正文标题措辞差异大（'Elements of statistical mechanics' vs
  'Statistical mechanics'）：超出模糊匹配上限，需更松的语义匹配，暂不接。
- 完整几何判别（垂直留白/对齐偏离/字号聚类定层级）尚未实现；
  已落地的是图注位置过滤与页首运行头收敛两个几何点。

## 病例 010｜全语料 / 字号阶梯（pdf-craft 方案的几何判别）

- **背景**：完整几何判别的落地。调研 pdf-craft（AGPL）后确认其核心武器
  是 `split_by_cv` 字号聚类——它不读 PDF 文本层（全书光栅化），"字号"
  就是 bbox 高度，与我们 bbox-only 输入同构。居中/留白/对齐它一律没用，
  证明那些不是必需品。**按算法思想重写，未搬代码**。
- **实现**（stage2_common.py）：
  - `_split_by_cv`：变异系数阈值 + 最大间隔二分，把标题块高度（bbox
    高，0..1，标题通常单行一块无需多行聚合）聚成字高档位（rank 0 最大）；
  - `_height_ladder_map`：用**锚定标题的真实层级**标定每个档位 →
    rank→level 映射（无锚定的档位向更浅的借一级）；
  - 接入 `_sink_unanchored_plain`：无编号无锚标题在下沉硬约束
    （不得浅于所属锚定章+1）之上，按字高档位**只许加深、不许上浮**——
    字大的小标题停在本章下一级，字小的继续沉。max_cv=0.1（宁细勿粗，
    误分只会在保守方向）。
- **回归**：单元测试 37/37；全语料终验见当轮汇报。
- **状态**：已实现并验证。

### 几何判别最终清单（三轮沉淀）

| 特征 | 用途 | 状态 |
|---|---|---|
| bbox 位置邻接（图下/表上） | 图注/表注过滤 | ✅ 病例 008 |
| 页首位置 + 前文同名 | 运行头收敛 | ✅ 病例 009 |
| bbox 高度聚类（split_by_cv） | 无锚标题层级差异化 | ✅ 病例 010 |
| 垂直留白/对齐偏离 | 评估后放弃（pdf-craft 也不用） | ❌ 不接 |

## 病例 011｜全局一致性定级（LLM 收口）+ 运行头阈值修正

- **全局一致性定级**（`_global_level_pass`，stage2_common，开关
  `GLOBAL_LEVEL_PASS`）：确定性规则把候选标题列表洗干净后，把全书
  标题表（ID/页/形状/锚定/文本）交给 LLM 一次定级，替代分块局部投票
  的漂移。锚定锁死、无编号标题受下沉底线钳制、层级钳 1..8、失败
  自动保持现有层级（安全降级）。
- **A/B 验证**：刘擎 0 调整（LLM 完全同意确定性路径——最强佐证）、
  CMP 30 调整（锚定 168/168 不变）、民法总论 105 调整（空章 6→13 后
  回落，见下）。
- **运行头阈值修正**：民法总论出现全章节重复，根因是页眉 y2=0.084
  恰好超过 0.08 阈值（本轮 LLM 投票波动使更多页眉被投成标题，暴露了
  阈值盲区）→ 放宽到 0.10，15 章零重复恢复基准。教训：**几何阈值
  必须留足 OCR 抖动余量，且要在多本书上标定**。
- **回归**：单元测试 39/39。
- **状态**：已实现并验证。

## 病例 012｜发版回归（高等数学/Born a Crime/城市与国家财富）— 匹配层收尾

- **现象**：发版回归 3 本全管线，4 条目录条目未锚上。
- **根因与修补**（全部是标题键归一化缺口，`_normalize_title`）：
  1. 公式节标题：目录写 `$f(x)=..$型`，正文是 equation 块
     `$$ 一、f(x)=.. 型 $$` → 剥 `$` 定界符对齐；
  2. LaTeX 排版命令：正文 `\left[ \right]`、目录裸括号 → 剥 `\left/\right`
     （残余 1↔i 一字 OCR 误差由模糊匹配兜住）；
  3. 弯直引号：正文大写直撇号、目录弯撇号（Born a Crime ch14 长标题）
     → 弯引号统一为直引；
  4. 超短尾部：章名碎块 'RUN'（3 字母）对 'Chapter 1: Run' →
     尾部匹配 len≥4 放宽到 ≥3。
- **边界（不接）**：Born a Crime 'Dedication' 正文无标题块（引擎漏识，
  内容完好）；高等数学/Born a Crime 各剩 1 条未锚属此类引擎边界。
- **回归**：单元测试 40/40；高等数学 140/140、Born a Crime 21/22
  （'Chapter 1: Run' 锚上）、城市与国家财富 16/16。
- **状态**：已修复并验证。


## 病例 016｜高等数学 / stage3 — 公式不居中复燃：双 display 属性 + 正则跨段吞并

- **现象**：8/15 修复后用户跑回归仍报"公式不居中、标题 heading level 不对
  （TOC 是对的）"。审计新 EPUB：2726 处 block 看似正常，但 257 处独占段落的
  显示公式仍 inline 贴左；且章标题（h2.chapter-title）与节标题（h2）同级，
  节标题渲染成居中大字与章视觉平级。
- **根因链（四条独立缺陷，层层掩盖）**：
  1. **双 display 属性**（`_latex_to_mathml`）：latex2mathml 的 convert()
     自带 `display="inline"`（标签尾部），我们再插一份 `alttext+display`
     → `<math alttext display="inline" xmlns display="inline">`。ebooklib
     序列化去重后 inline 胜出——promote 替换第一个属性后仍被第二个抵消，
     升格全部落空（$$ 定界公式的 block 同病）；
  2. **正则跨段吞并**（`_P_LONE_MATH_RE`）：math 部分 `.*?</math>` 懒惰匹配
     允许跨 `</p>`——前段 math 的 post 超长匹配失败时回溯扩展到**后段的**
     `</math>`，形成跨段大匹配；repl 按前段 latex 判定不合格即整体原样
     返回，**后段（如 '(1) f(x)=\left\{...' 例题）永远失去单独匹配机会**。
     此 bug 单段调用永不暴露（单测全绿、探针全升、整文却漏），且反向造成
     误升（大匹配判定通过时连带升格前段的行内引用公式）；
  3. **pre 过严**：`_PUNCT_NUM_RE` 剥空才放行——"解/证/双曲正弦/例1设"等
     短叙述前缀的整段显示公式 74 处贴左（附录习题答案重灾区）；
  4. **标题层级映射**：popo 路径 `h{min(level,6)}` 直用结构层级，节与章
     撞 h2（章标题固定 h2）。
- **修补**（stage3_epub.py）：
  - `_latex_to_mathml`：替换 latex2mathml 自带 display 的**值**，不再另插；
  - `_P_LONE_MATH_RE`：math 部分改 tempered token `(?:(?!</p>).)*?` 禁跨段，
    `replace(..., 1)` 改全量替换（防御双属性残留）；
  - pre 放宽：`[^<$]{0,12}` + 剥标点数字编号后 ≤4 字放行（post 保持严格，
    行文行内引用一律被 post 挡住）；`_PUNCT_NUM_RE` 补圈号 ①-⑳ ㈠-㈩；
  - 标题层级：章内标题按相对章深度偏移（节 → h3、小节 → h4，两条渲染
    路径同规）；CSS h3 改居中（贴合原书节标题居中排版）。
- **回归**：新增 tests/test_stage3_promote.py 12 例（含双 display、跨段吞并
  场景——后者复现整文/单段分歧）；结构单测 40/40；高数重跑 block 2726→
  3223（误升消退后净升 497）、独段 inline 257→156（残余=短答案行不该升）、
  '(1) f(x)' 段升 block、章 h2/节 h3 层级正确、QC formula_artifacts 0。
- **边界（不接）**：multi_math 13 处——同一公式被引擎拆成两块（block 主体
  已居中，"+C" 尾巴 inline 残留），合并 MathML 过于脆弱，记引擎层边界。
- **状态**：已修复并验证。

### 病例 016 补记（同日二轮）——第五连环：CSS 破坏 UA 的 block math 布局

- **现象**：上述修复全部落地、EPUB 文件验证无误后，用户在 SageRead 实例里
  看到的公式**仍然全部贴左**（截图实证）。
- **排查**（CDP 穿透 foliate 的 closed shadow root 实测 computed style）：
  block 公式的 `text-align: center` 已生效，但**视觉贴左**——因为
  DEFAULT_CSS 写了 `math[display="block"] { display: block; }`，把 UA
  样式表的 `display: block math`（MathML Core 的块级数学布局，内容自动
  水平居中）覆盖成普通块盒，数学内容 shrink-to-fit 贴左；text-align 管
  不到数学布局内部。A/B 实证：把 display 恢复 `block math` 后整页公式
  即刻居中。
- **修补**：
  - 转换侧（stage3_epub.py DEFAULT_CSS）：删除 `display: block` 声明，
    保留 text-align:center 作冗余保险——新产物不再破坏 UA 布局；
  - 阅读侧（SageRead `utils/style.ts` 注入样式）：显式
    `math[display="block"] { display: block math; text-align: center; }`
    ——**存量旧产物无需重转**，注入样式统一兜底（SageRead commit 同步）。
- **教训**：EPUB 文件级验证（解包看 HTML/CSS）与渲染级验证（阅读器实测）
  必须都过——本案文件完全正确、渲染却是坏的；"315/315 居中"的验证当时
  只抽查了 display 属性，没看最终像素。
- **回归**：实例内打开未重转的 v1.3.2 版高数（旧 CSS），注入样式兜底后
  习题区行列式/分式/编号公式全部居中，节标题层级正确。
- **状态**：已修复并验证（exe/zip 重打，Release v1.3.2 资产已更新）。


## 病例 015｜高等数学 / MinerU—stage3 — 显示公式被降级 inline 贴左

- **现象**：8/15 干净重跑后用户仍报"块级公式不居中"（对照外部 z-lib 图片版
  居中）。DOM 实测 display=block 的公式全部精确居中——不居中的根本不是
  它们：审计发现 **840 处显示型公式（例题 y=frac…;、定义域映射、推导链）
  被标 display="inline"**，独占段落却按行内排，吃 2em 首行缩进贴左、行内
  字号。
- **根因链**：MinerU 把显示公式以单 \$…\$ 定界输出；_mathmlify 的行内分支
  照单全收转 display="inline"。（8/4 paddleocr 脏解析事件修复后，同一本书
  剩余的"不居中"观感即源于此——与阅读器无关。）
- **修补**（stage3_epub.py）：`promote_lone_display_math` 段落级后处理——
  整段唯一数学区 + LaTeX 含显示结构命令（frac/left/sum/…）且长于 25 字符
  + 前后仅标点/编号 → 升 display="block"；叙述前缀（"双曲正弦"/"例 1 设"/
  "，即…"）的行内引用不动。四个内容发射点（正文/前置/后置/ch_html）接线。
- **回归**：高等数学重跑，block 2008→2726（升格 718 处），剩余 183 处
  独段 inline 均带叙述前缀属正确行内；应用内实测第一章 315 个可见块级
  公式 315 居中 0 偏心；升格判定单测 6 例全绿。
- **状态**：已修复并验证（exe 已重打同步双实例 binaries）。

## 病例 013｜QFT 两本书 / 打包 — 全书公式退化为 LaTeX 源码

- **现象**：A Modern Introduction to QFT / QFT and the Standard Model
  转出的 EPUB 全书公式都是 `<code class="latex">` 裸 LaTeX 源码（阅读器
  原样显示源码）；同期高等数学（外部 EPUB，前缀式 MathML）渲染正常，
  排除阅读器侧嫌疑。
- **根因链**：latex2mathml 的符号表 `unimathsymbols.txt` 是包内数据文件，
  `symbols_parser` 运行时 open 读取；`books_converter_cli.spec` 的
  hiddenimports 只收模块不收数据文件 → frozen exe 里符号表缺失 →
  每条公式转 MathML 都 FileNotFoundError，被 `_latex_to_mathml` 的
  `except Exception` 吞成 None → `_mathmlify` 全量走 `<code>` 兜底。
  无公式书不触发，此前未暴露。
- **修补**（books_converter_cli.spec）：`datas=collect_data_files('latex2mathml')`
  （与 papers_converter_cli.spec 收 pypinyin 数据同款方案）。
- **回归**：archive_viewer 确认新 exe 含 unimathsymbols.txt；旧 exe 从
  staging 重跑复现（chapter_004: 0 MathML/126 code），新 exe 同 staging
  重跑（全书 16609 MathML/1 兜底）；两本坏书已从 staging 重跑 stage3
  并原位替换 book.epub，应用内 foliate 实测 1108 math/1044 渲染/0 兜底。
- **状态**：已修复并验证（exe 已同步 SageRead 双实例 binaries）。

## 病例 014｜QFT 两本书 / stage3 — 表格·表注·图注·标题里的公式漏转

- **现象**：正文段落公式已渲染（病例 013 修复后），但表格单元格、表注/
  图注、章节标题里的公式仍是裸 `$…$` 源码（如 h4 "26.3 $e^{+}e^{-}
  \rightarrow$ hadrons"、td "$\left(\frac{1}{2},0\right)$"）。
- **根因链**：`_mathmlify` 只挂在正文段落渲染路径（_render_popo_body 的
  seg 处理）；标题（`<hN id>`/chapter-title/part-title 四处发射点）、图注/
  表注（caption_map/small/strong）、表体（MinerU HTML 原样透传）、列表项
  均未过公式转换。旧 `_render_block_to_html` 路径同病。
- **修补**（stage3_epub.py）：
  - 上述全部发射点补 `_mathmlify`（alt 属性除外——属性值不能放 MathML）；
  - equation 块兼容 latex/text 字段形态：纯 LaTeX 也尝试转 MathML，
    失败才退 `<code class="latex">`；
  - DEFAULT_CSS 表格改 `width: fit-content; max-width: 100%;
    margin: 1em auto`（窄表按自然宽度居中，宽表占满列宽——旧版
    width:100% 把所有表拉满整行）。
- **补丁（同日二轮）**：前置/后置物质走旧路径 `_render_block_to_html`——首轮批量
  补丁脚本因末对断言失败在写盘前退出，该路径的图注/表注/表体补丁未落盘
  （高等数学回归实测附录图注裸 $y=rctan x$）；Edit 补齐后高等数学
  11212 MathML、Standard Model 47/16/25 无回归。GUI spec 同步补
  latex2mathml hiddenimport+数据文件（GUI 版同病）。
- **回归**：两本 QFT 书从 staging 重跑（--skip-mineru --skip-deepseek），
  EPUB 内 td math 47/41、注 16/31、标题 25/5，裸 $ 清零；应用内 foliate
  实测单元格/注/标题 MathML 渲染、表格与 block 公式均居中。
- **状态**：已修复并验证（exe 已重打同步双实例 binaries）。

## 病例 017｜Calculus Made Easy（Gutenberg 公版书）/ stage2 — 无目录书 LLM 编造假目录，层级毒化

- **现象**：born-digital 公版书无印刷目录页，轻量元数据 pass 的 LLM
  不返回空 toc_entries，而是拿 prompt 附带的【全书标题列表】编造一份
  假目录（page 直接抄标题块的扫描页码）。假条目经锚点以最高优先级锁死
  错误层级，形状栈/字号阶梯全被跳过，文档树结构散架。
- **根因链**：LLM 面对"提取目录"指令不会空手而归——采样页里没有目录页，
  就拿现成的标题列表拼一份；且 page 抄扫描页（真目录应给印刷页，与扫描页
  通常有非零偏移），形成确定性的伪造指纹。
- **修补**：
  - stage2_common.py `finish_structure` 伪造目录双判定硬兜底：
    判定一（主）两个目录页识别器均未命中 → 判定无印刷目录，丢弃 LLM
    toc_entries；判定二（双保险）`_forged_toc_fingerprint`——≥5 条可比对
    且 ≥80% 条目页码与标题块扫描页完全相等 → 丢弃。丢弃后回退
    形状栈+编号先验路径；
  - `_LIGHT_PROMPT` / `_LIGHT_TOC_PROMPT` 软约束：无目录页必须输出 []，
    严禁编造（软约束不足以依赖，故需硬兜底）；
  - `setdefault` → `or` 防御：LLM 把字段输出成 null 时 setdefault 挡不住
    （None 不是缺失键）；
  - pipeline.py `_read_pdf_outline`：fitz get_toc 读取 PDF 书签
    （born-digital PDF 的免费结构真值，1 起物理页码与扫描页同 regime），
    经 stage2_hybrid 透传 `pdf_toc`，非空时取代 LLM toc_entries 且不参与
    伪造判定；扫描本无书签 → 返回 []，原流程不变；
  - qc_book.py 连带：无目录书不再当硬错误，降为黄牌提示。
- **回归**：tests/test_stage2_toc.py 新增伪造指纹用例，全套 41/41 +
  stage3 12/12 绿；伪造条目回放走丢弃路径实测通过；Calculus Made Easy
  （Gutenberg #33283）60 页重转目录两级正确、477 MathML 居中。
- **状态**：已修复并验证（源码路径；exe 重打与 SageRead 同步随下次发版进行）。

## 病例 018｜必须保卫社会 / PaddleOCR — 跨页段落切断未合并

- **现象**：EPUB 中一个段落被按页切成多个 `<p>`：上一页段落以文字或逗号结尾
  （无句末标点），下一页开头小写字母接续句中。SageRead 书籍对照翻译侧实测
  暴露：章首目录式短行五行本是一句，模型翻译时把碎片合并重分段导致译文错位
  （SageRead 侧已另行加固批响应逐条校验，见 docs/book-translation-plan.md）。
- **根因链**（2026-08-29 调研，未修）：Stage1 按页出块（段落边界=页内视觉块
  边界）→ 跨页合并唯一判定源是 stage2_hybrid LLM 标注 `contd`
  （_SYS_CONTD 无"下段小写起首"线索、解析失败静默丢）→ `filter_contd`
  （popo/inference.py:239-240）遇 title/equation 块 break，页眉挡住候选对
  → stage3_epub.py:1051 合并要求 open_para 仍开着，页首 title/image/table/
  孤儿 caption 均会重置 open_para，contd 标对了也静默不并 → 正则兜底
  `_merge_broken_paragraphs`（stage3_epub.py:233-259）只挂在旧引擎
  `_render_chapter_html` 路径，生产 popo/hybrid 路径完全不经过。
- **修补点**（方案已定，择期实施）：
  1. 首选：把 Papers_Converter 的规则合并（content_processor.py
     `_merge_paragraph_fragments`：前块无终止标点 + 后块小写/开括号起首或
     前块逗号/虚词结尾 → 并；跨页与隔图表情景齐备）移植为 popo 路径兜底，
     挂在 stage3_epub.py `_render_popo_body` contd 分支之后规则回捞；
  2. `filter_contd` 不在页首 title 块处 break（至少跨过运行头）；
     `_SYS_CONTD` 补小写续接线索；
  3. qc_book.py 加段落级检查（页末无终止标点+次页首小写未合并对计数）。
- **回归**：待实施时补——规则合并单测（对照 Papers 侧信号集）+ 本书
  chapter_001 章首五行合为一段的回放用例。
- **实施记录**（2026-08-30，修补点 1 落地；2/3 未做，仍挂账）：
  - `_BROKEN_P`/`_merge_if_broken` 废弃，`_merge_broken_paragraphs` 重写为
    扫描制（`_P_TAG` 段内允许任意内联标记——旧 `[^<]` 形态下含行内公式/
    sup/em 的段完全不参与合并，高数产物 10252 个 `<math>` 段全被跳过）；
    带 class 的段（no_indent 图注/footnote）是硬边界，不参与也不被跨过。
  - 判定重构为信号制 `_should_merge`：合并 =（p1 非句末标点收尾 OR p2
    小写续行 OR p2 极短碎片）AND NOT 公式例外（p1/p2 任一为零叙述字
    孤公式段——高数实测 p2 孤公式 1953 处、p1 孤公式 637 处，不加闸
    即大规模跨公式误并）AND NOT 既有三护栏（脚注尾/编号短段/疑似标题，
    疑似标题双向）AND NOT 短段碎片闸（p1 <10 可见字且无标点收尾）。
    `_SENTENCE_END` 补 ASCII `.` 与全角 `：；`（旧漏 `.` 致英文句号收尾
    段落被判"未完结"，模拟实测 40 处误并）。
  - popo 生产路径兜底：`_render_popo_body` contd 分支后挂 `_rule_merge_ok`
    ——跨页用完整信号体系；同页仅小写续行强信号（同页相邻块多为有意分段，
    版权页/目录行/页脚注释行实测）；链长上限 `_MERGE_CHAIN_MAX=6`。
  - 拼接 `_join_inners` 口径同 `_dehyphen_join`：断词去连字符、英-英补
    空格、中文直拼；前页/分隔页引言（前言散文）经 `_render_pages_html`
    合并，后页传 `merge=False`（索引/习题答案/积分表"无标点短行成排"是
    误并重灾区——必须保卫社会索引 291 段被并掉 136、高数答案区条目互粘，
    实测后切除）。
- **回归**：tests/test_stage3_merge.py 新增 25 例（合并/拼接/公式例外/
  护栏/链闸），全套 25/25 + promote 12/12 + toc 41/41 绿。
  真前基线对拍（同源码 stash 来回）：必须保卫社会 `<p>` 1330→1063
  （-267=正文 256+前页 11），高数 8006→7977（-29=正文 26+前页 3），
  两书 block math 3180→3180/0→0 不变、后页与索引零改动。
  合并点随机抽 20 人工读：18 完全正确，2 处判定正确但拼接残留 OCR
  无连字符断词空格（"knowledge edge"/"app paratus"，PaddleOCR 丢连字符，
  判定层无法还原）。未并的"无标点收尾"点抽 10 全属例外（孤公式 4、
  同页页脚注释行 4、图注/编号 2）。
- **已知残留**：① 无连字符断词（OCR 丢 `-`）合并后留空格；② 同页中文
  碎行不并（无小写信号，保守）；③ 诗歌/信件等无标点成排体裁若 p1 ≥10
  字且非标题形状仍可能误并（链长 6 兜底）；④ 修补点 2（filter_contd
  跨页首 title）与 3（qc 段落级检查）未做。
- **状态**：已修复并验证（2026-08-30；与 SageRead 翻译侧各自独立生效）。

### 追记（2026-08-31）｜高数 TOC 稀碎疑案——合并改动洗清，真凶是病例017 的 PDF 书签先验

- **现象**：用户用最新代码重转《高等数学》（MinerU+hybrid），EPUB 导航
  目录从 72 条（章/节两级）碎成 191 条扁平垃圾条目（"一、映射／1"
  "习题1-2／26" 等印刷目录页行带页码尾巴），正文标题也带"／页码"。
- **洗清合并**：同一份 stage2 产物（structure.json + popo_blocks.json）
  灌进新旧两版 stage3_epub.py 对拍，nav TOC 逐字节相同（各 191 条）——
  本病例的合并改动不背锅；合并本来就把 h 标签当中间断链硬边界
  （test_non_adjacent_not_merged），本轮另补 test_heading_tag_not_swallowed
  锁死。
- **根因链**：该 PDF 自带第三方自制书签，形态为"标题／页码"（全角／+
  印刷页码）且 level 全 1 → 病例017（463572a）引入的 `_read_pdf_outline`
  把 PDF 书签当"确定性真值"**无条件取代** LLM toc_entries，伪造目录硬
  兜底又被 `not pdf_toc` 守卫跳过 → `_TRAIL_PAGE_RE` 不含全角／剥不掉
  页码尾巴 → 锚点富化 `b["content"]=m[2]` 把脏书签文本写进正文标题块
  → `_spine_from_toc` 见全平层级把 174 个书签行全切成章。旧版（08-17）
  转换早于 463572a，LLM 从印刷目录页提取的条目干净带层级，故 TOC 正常。
- **修复**：stage2_common 新增 `_sanitize_pdf_toc`（≥80% 条目带"／页码"
  尾巴即整体拒收，回退 LLM 目录提取；零散尾巴逐条剥除），挂在
  finish_structure 采用 pdf_toc 之前；`_TRAIL_PAGE_RE` 与 stage3
  `_build_toc_lookup` 的展示清洗同步补全角／。`not pdf_toc` 守卫保留
  （合法 PDF 书签的 page 本就是物理页，过指纹判定必误伤）。
- **回归**：tests 27/27（merge，+2）+ 44/44（toc，+3）+ 12/12（promote）
  绿；真实脏书签（198 条，即事故 structure.json 的 toc_entries 真身）
  注入重跑 stage2+stage3：书签被拒收（日志"193/198 条带页码尾巴"），
  LLM 目录 144 条层级 1/2/3 干净，nav 72 条与旧版逐条一致（仅 4 条前页/
  附录标签措辞差异，LLM 固有波动），正文 h2/h3/h4/h5 层级正常、全书无
  "／页码"残留；合并函数零改动（git diff 仅触及 _build_toc_lookup 一行
  正则与 stage2），必须保卫社会 267 处合并无回吐机制。

## 病例 019｜Feeling Great / PaddleOCR — 目录采样截断 + 退化锚点毒化全书层级

- **现象**：544 页英文书转换成品目录稀碎——nav ~108 条混入 '1. _____'
  （填空行）/'总计'/'我说：'/'钦定四庫全書'（PaddleOCR 幻读块）等垃圾 L1 章；
  真第 11–19 章从目录消失；同章出现'编号版+无编号版'双条目；fnref_* 脚注
  回链混进目录。QC 却报 anchor_hit 25/25 全绿。
- **根因链**（六个独立缺陷叠加，均为 stage2/stage3 自身逻辑，与 OCR 无关——
  实测 PaddleOCR 章首块 text_level=1 与两页印刷目录识别均完好）：
  1. `_PAGE_CHARS=800` 截断：两页印刷目录 1263/953 字符被腰斩 → toc_entries
     缺 11–19 章与 Section II，并产生退化条目 'VI.'（无文字无页码）；
  2. `_match_anchor` 模糊兜底 off-by-one：`best_dist` 起始 3，而
     `_edit_distance_le` 超限返回 limit+1（短锚点=2）仍被接受 → 所有 4–7
     字符短块（total/1._____/•mania/isaid:/欽定四庫全書）全部模糊命中 'vi.'；
  3. 锚点救援无视觉证据闸门：纯文本块命中即晋升并 `_anchored` 锁死，
     124 处救援混入全部垃圾；垃圾 L1 再污染 `_sink_unanchored_plain`/
     全局定级的"最近锚定块"地板 → 真 12 章被压到 L3 掉出 nav，
     13 章靠前一个垃圾锚洗地板反而幸存（层级扭曲具有系统性）；
  4. `extract_label2` 接受合同外 level=0 → 第 18 章成"是标题但所有
     level>0 过滤都看不见"的僵尸块，被 stage3 当正文并入上一章；
  5. `_spine_from_toc` 只认 part/chapter/第X章词形，英文 'I. …'/'1. …'
     纯数字目录全失配 → spine 回退启发式=最小层级 1 → 垃圾 L1 全部开章；
  6. 跨页近似重复零去重（'4 Karen's' vs '4. Karen's' 标点之差）+ subs
     分支无条件 append + ebooklib `epub3_pages` 默认把 noteref 回链收进
     page-list。
  7. QC 虚绿的机制：`_light_metadata_pass` 只检查"toc 里的条目是否锚上"，
     缺失章不在 toc 里就永远不可见；且复用同一个有 off-by-one 的
     `_match_anchor` 批自己的卷子。
- **修补**：
  - stage2_common：`_PAGE_CHARS` 800→4000；`_build_anchors` 拒收有效字符
    <3 且无 CJK 的退化键；`_match_anchor` 模糊候选必须 d≤limit（超限即弃）；
    `_SHAPE_PATTERNS` 新增 num_bar（'12 | Title' 竖线编号，否则按 plain
    被下沉机制误压）；`_calibrate_levels` 救援加**位置闸门**——已锚定标题
    估计印刷页→扫描页偏移，纯文本块须落在预测 ±8 页内（杀 CliffsNotes
    精华章里逐条命中锚点的章节摘要表交叉引用）；引擎已标标题的块豁免闸门
    （前置页罗马页码/附录另起页码 regime 下全局偏移本不成立——
    'Acknowledgments' ix、刘擎'补充讲解' 289→371 两道误杀实测追回）。
  - popo/inference：`extract_label2` 拒收 level=0（块保持推理前状态，
    交锚点救援兜底）。
  - stage3_epub：`_PARTITION_HINT` 补罗马数字分区（'I. …'/'Section IV'）、
    `_CHAPTER_HINT` 补纯数字编号章（'12. …'）；`_similar` 判重去标点/
    大小写（跨页近似重复合并）；subs 分支补相邻判重；`write_epub` 关
    `epub3_pages`（管线本就不产 pagebreak 锚点）。
  - qc_book：新增"孤儿章节标题"反向校验——正文章节编号形状的标题块无
    目录条目对应即报（显式'第X章/Chapter'红、纯数字黄），专杀"锚定全绿
    但成品缺章"的盲区。
- **回归**：tests/test_structure_rescue.py 新增 24 例（截断/退化锚/模糊
  off-by-one 正反例/level=0/spine 词形/num_bar/位置闸门与豁免），
  既有套件 toc 44/44、merge OK、promote OK 全绿。
  真书验证（--skip-mineru 复用缓存）：Feeling Great 目录条目 29→43 全量、
  partition=1/spine=2 正确识别、锚定 38/38、nav 主干 33 章+6 分区有序完整、
  垃圾条目清零、fnref 清零；刘擎西方现代思想讲义既有红牌'答学友问未锚上'
  转为 55/55 全锚定；伊豆の踊子（无目录页书）新旧 nav 等价无回归。
- **残留**（引擎已标标题块不参与位置闸门，属视觉证据优先的既定取舍）：
  书末索引/推荐页里被 VLM 标成标题的分区名（'III. …'/'V. …'）与正文
  '26. Let's Be Specific' 引用框，共约 6/217 条 nav 错位/重复。
- **状态**：已修复并验证（2026-09-03）。SageRead sidecar exe 需择期重打
  （binaries/books_converter-x86_64-pc-windows-msvc.exe 仍为旧码）。

## 病例 020｜Feeling Great / stage4 — 批响应缺号静默收编 → 整批译文错位并经续翻缓存扩散

- **现象**：病例 019 结构修复后的成品的章题译文错位——'11 | The Great
  Escape' 章题显示为 '2. ___'、'10 | …' 块译文为 '1. 全或无思维。…'
  （别块的译文）；整章从 nav 消失（译文垃圾化后被相似判重吞并）。
- **根因链**：`_translate_batch` 按序号 1..N 静默映射批响应，模型合并
  短碎片/跳号时响应条数 < 批条数，其后所有条目映射整体后移错位；
  错位译文写入 translations.json 断点缓存，续翻/重跑无条件复用 →
  结构修复后的二跑仍继承毒化译文（SageRead 侧 018 已加固同款校验，
  本书侧漏网）。
- **修补**（stage4_translate.py `_translate_batch`）：只接受 1..N 全覆盖
  且非空的批响应；缺号/非字典即抛错进既有 重试→拆半 自救链；
  失败方向 = 保留原文，绝不产出可疑映射。
- **回归**：tests/test_stage4_translate.py 7 例（全覆盖对位/坏响应全批
  留原文/顶层跳号拒收+拆半自救/首缺后拆半齐全）。真书验证：删除毒化
  translations.json 全量重译（4566 条），'11 | …大逃亡' 等全部章题
  译文归位，nav 由 206 条（含幻影）收敛到 129 条干净结构。
- **备注**：拆半到单条粒度后，"单条批的紧凑缺号"在原理上不可检测
  （{1: …} 对单条批即是全覆盖）——残余风险仅限单条文本错误，无整批
  错位可能。
- **状态**：已修复并验证（2026-09-03）。

## 病例 021｜Feeling Great / stage2 — 无编号标题层级错乱三连修：子串覆盖率、锚点身份查重、垃圾否决器

- **现象**（019 修复后的残留）：幻影 L1 分区'抑郁'吞没 4–11 章；'具体化'
  （worksheet 框标题）成 L2 章；索引字母字头 'A'/'I'/'V' 上目录；署名行
  '马克·诺布尔博士 著' 成章；同章双目录（'5 梅兰妮'/'梅兰妮'）因译文措辞
  差异（梅兰妮/梅琳达）逃逸显示层查重。用户实测确认：字号聚类在该扫描件
  上无分离度（真章标题 bbox 高/页中位行高比值 0.12–6.11，嫌疑人
  0.44–1.47，完全重叠；同一章两次识别可差 2.4 倍）——几何信号只能维持
  "只许下沉"辅助地位，层级修复必须靠文本/结构机制。
- **根因链与修补**（均在 stage2_common.py）：
  1. `_match_anchor` 子串规则无覆盖率要求：单词块 'Depression'（10 字符）
     子串命中 41 字符分区条目 'I. How to Turn Depression…' → 锁 L1 + 富化
     改写内容，source_id 仍指原块 → 译文查询取回 '抑郁'（幻影分区本体）。
     **修**：子串命中须 len(块) ≥ max(8, 0.4×锚长)。
  2. 前缀规则无最低长度：单字母 'A' 前缀命中 'Acknowledgments'。
     **修**：前缀命中须 len(块) ≥ 3 或含 CJK。
  3. 跨页重复无"身份"判据：**新增 `_dedup_anchored_titles`**——命中同一
     目录条目的多个标题块只留一个。"留谁"用**目录序三明治一致性**（唯一
     命中锚点作骨架，候选须落在骨架相邻锚点的目录序区间内），不依赖印刷
     页码偏移（免疫附录另起页码/罗马页码 regime，刘擎'补充讲解' 289→371
     不受影响）；一致者优先，平级留阅读顺序首个（章题页先于章首重复页）。
     教训：首版用"印刷页+局部偏移最近者"被重复印刷的章题野票带偏
     （把含正文章节开篇的块降格、章节起始错位 2 页），实测后改三明治法。
  4. 无锚 plain 标题无否决器：**新增 `_veto_junk_titles`**——单字母/
     单 CJK 字/署名行（著译主编/by X）/单个英文词的无锚无编号块直接降回
     正文；锚定块豁免。只降格不晋升，失败方向安全。
  5. 下沉地板试验失败回滚：曾试"最近章级锚定"双锚取严地板，破坏
     刘擎'答学友问'系列（父 L1 子 L2 被压成 L3，tests/test_stage2_toc.py
     test_sink_after_rescue_ordering 拦截）——既有"最近锚定+1"语义正确，
     不动。
- **回归**：tests/test_structure_rescue.py 扩至 33 例全绿；既有套件
  toc 44/44、merge OK、promote OK、stage4 7/7 全绿。
  真书（全部结构-only，不翻译）：Feeling Great nav 147 条、六分区 33 章
  有序完整、垃圾条目清零、ch12 章首正文归位；刘擎 QC 全绿（55/55）；
  伊豆の踊子（无目录页书）无变化无回归。
- **残留（可接受）**：'Section II'/'II. …' 分区扉页与锚点双 L1 并存；
  'About the Author' 尾部两条；章首重复标题降格为正文段落会在章首
  h2 后多显示一行同文标题（内容不丢原则的代价）；ch33 技巧 1–4 嵌在
  32 章下（表格跨页边界）；QC 对 _rescue_by_page 的合法合成块仍报
  幻影 RED（该规则保守，本书合成位置正确）。
- **状态**：已修复并验证（2026-09-03，结构-only；翻译择期再跑）。

## 病例 022｜Condensed Matter / PDF 书签先验 — 通用 'Chapter N' 书签名无文本锚定力（挂账待修）

- **现象**：419 页教科书，PDF 自带 31 条书签但条目名为 'Chapter 1'…
  通用词（无真实标题文字）→ 正文标题块是 '1 Crystal structure' 形态，
  文本锚定全灭（anchor_hit 12/31），章/节/小节层级坍缩为平铺 L2；
  页码救援按位置合成出 '9781107017108'（ISBN）幻影标题。
- **根因**：`_sanitize_pdf_toc` 只防'标题／页码'假目录，不识别"通用名"
  书签树的信息量缺失；锚定体系全部建立在文本相似上，对"纯泛名"条目
  无计可施。书签页码是扫描页真值（无印刷/扫描偏移问题）这一免费信号
  闲置未用。
- **修补**（2026-09-05 实施并验证）：
  - `_sanitize_pdf_toc`：纯数字长串条目（ISBN '9781107017108'）拒收。
  - `_normalize_generic_outline_levels`（新）：全平 outline（Part/Chapter
    同层）含泛名条目时按标签类别重建层级（Part→L1、Chapter→L2）——
    否则 `_spine_from_toc` 读到平层，spine 退化为 1，章/节全被压平。
  - `_anchor_generic_outline`（新）：泛名条目按书签页（扫描页真值，无
    印刷/扫描偏移）锁定该页首个未锚定标题块；页内无标题块时晋升首个
    类标题文本块（书签页=章首页强证据，CM 第 2 章章题未被标出照样归位）。
  - stage3 `_render_popo_body` 章级判重只许同 kind 合并：'STRUCTURE'
    分区页与 'Crystal structure' 章仅一词重合，跨 kind 包含判重把四个
    Part 的首章全吞进分区页（既有隐藏 bug，被层级修复后暴露）。
  - stage3 `_body_range`：泛名 outline 条目页码回收正文起点（LLM 前页
    分类波动把 p22 分区页划进 front_matter 的实测波动，toc_source=
    outline 标记防止印刷页 LLM 目录误用）。
  - qc_book：`_pos_anchor` 位置锚定块计入锚定命中。
- **回归**：tests/test_structure_rescue.py 扩至 49 例全绿（泛名识别/
  ISBN 拒收/位置锚定/未标出晋升/层级重建/spine/正文起点回收）；
  既有套件全绿。CM 真跑：anchor_hit 12/31 → 30/30，PART I–IV 分区
  下 18 章正确嵌套、节/小节归位、Summary/Exercises 保留；
  FG/刘擎/Born a Crime/German Ideology 护栏无回归。
- **残留**：CM 第 2 章章题取到 'Introduction'（晋升按页内阅读顺序首个
  文本块，该页布局如此）；'PART I'/'STRUCTURE' 分区扉页与锚点双 L1 并存
  （与 FG 'Section II'/'II.' 同类妆饰级重复）；'Front' 合成幻影 1 个。
- **状态**：已修复并验证（2026-09-05）。

## 病例 021 追记（同日二轮）

- 单词否决器误伤复发型章末小节：'Summary'/'Exercises' 每章复发，
  初版单 token veto 全部降格。**修**：无锚 plain 短标题复发 ≥3 次豁免
  （复发=教科书固定小节的稳定特征）；字母/孤字/署名否决不豁免（索引
  字母字头同样复发）。单元测试 34/34。
- 英文书结构-only 回归（暂存副本全量）：Born a Crime（431 页，参考
  EPUB 对照）Part I–III 与 ch1–18 全对，残差=三个 150+ 字符超长章题
  （ch10/12/14，多行 OCR 拆块超文本匹配上限，既有局限）与 Part I 章
  平铺未嵌套；The German Ideology（1047 页，无目录页无书签）形状栈
  路径产出干净德文学术结构（I. Feuerbach A/B/C、II. Sankt Bruno 1–4、
  III. Sankt Max…）；Condensed Matter 暴露病例 022（既有洞）。

## 病例 023｜QFT（A Modern Introduction）/ MinerU — spine 词形过度匹配压平层级 + 章首 mini-TOC 误伤与运行头顶替

- **现象**（v1.3.5 sidecar 转换产物，用户实测发现）：
  1. 第 5 章与 5.1–5.9 节在 nav 全部平级（旧版层级正确）；
  2. 第 4 章标题标记落在章开启之后两页的运行头上，真正的章首句
     'From the basic principles of quantum mechanics…' 溜进 3.7 习题
     小节末尾（"电影播了十分钟才标开头"）。
- **根因链**（三个独立缺陷）：
  1. `_CHAPTER_HINT` 的纯数字编号词形 `\d{1,3}[.、．)]\s*\S` 不区分单级
     与多级编号：'1.1 Overview'/'5.5.1 …' 这类小节条目被误判为章，
     chap_lv=2 → spine=2 → stage3 把所有 L1/L2 块都开成独立章（压平）。
     **修**：分隔符后加 (?!\d) 负向前瞻，多级编号不再命中（病例 021
     为 Feeling Great '12. …' 引入词形时的过匹配）。
  2. 章首 mini-TOC 页（本书每章开头印有带页码的小目录）与全局目录共享
     "≥3 条目行+数字块"形态，被 `_repair_toc_pages`/
     `_detect_toc_pages_by_entries` 一并标为目录页 → 真章题随降格消失
     （实测 ch3/4/5 的章首页 58/98/195 全中）。**修**：目录页降格只作用于
     书首 ≤25 页（全局目录永在前页；章首 mini-TOC 必在深页）。
  3. 真章题消失后，锚点救援不设类型闸门，把下一页 suffix 命中的运行头
     （header 噪声块）晋升为章标题并锚定——标题标记整体后移一页。
     **修**：救援跳过 header/footer/page_number/aside_text/discarded
     噪声类型（真章题获救后由 2 的修复保证，运行头再无机可乘）。
- **回归**：tests/test_structure_rescue.py 扩至 51 例全绿（多级编号词形/
  运行头不晋升）；既有套件全绿。QFT 真跑（mineru 缓存）：5 章下 5.1–5.9
  正确嵌套、ch4 标题归位且章首句回本、anchor_hit 133/133；
  FG 38/38、刘擎全绿、CM 30/30 护栏无回归。
- **残留**：章首 mini-TOC 行被锚定为节标题时带页码尾巴
  （'4.1 Scalar fields 83' 标签不美观，内容归位正确）；空章黄灯若干
  （mini-TOC 行空壳章）。
- **状态**：已修复并验证（2026-09-05，sidecar v1.3.6 已部署）。

## 病例 024｜QFT / stage2 锚定 — 'Problem N.M.' 编号前缀差导致习题节锚不上

- **现象**：习题章的节标题（'Problem 3.1. The fine structure of the hydrogen
  atom'）锚不上目录条目（'The fine structure of the hydrogen atom'——印刷
  目录省略 Problem N.M. 前缀），11 条节标题未锚（133/144）；且因 LLM
  提取目录时是否保留前缀而呈现偶发波动（前一轮曾 133/133）。
- **根因**：锚点匹配的 exact/prefix/suffix 规则都不覆盖"块=编号前缀+条目"
  形态；编辑距离因前缀过长超限。编号前缀是 OCR 与目录的系统性差异形态
  （物理/习题类教科书普遍）。
- **修补**（stage2_common.py `_match_anchor`）：新增 `_PROBLEM_PREFIX_RE`
  剥离键——problem/exercise/example/aufgabe/problème/exercice/习题/例题/
  问题/练习/思考 + 数字编号，剥掉后补一轮精确命中；严格限定词表且必须带
  数字编号（'Problems' 不触发），只补精确、不进模糊兜底（防过匹配）。
- **回归**：tests/test_structure_rescue.py 扩至 55 例全绿；既有套件全绿。
  QFT 真跑 142/144（剩 2 条为公式节标题，另一类问题）；FG 38/38、
  刘擎全绿、CM 30/30 护栏无回归。
- **残留**：公式型节标题（'$K^{0}\rightarrow\pi^{-}l^{+}\nu_{l}$' 等）
  锚不上——LaTeX 公式串与目录 OCR 形态差异大，需要公式归一化专题处理。
- **状态**：已修复并验证（2026-09-05，sidecar v1.3.7 已部署）。

## 病例 022 追记（2026-09-05 三轮）：CM 章题漏标/误标与裸章号

- **现象**（用户比对 PDF 实锤）：ch11/12/13 一级标题丢失（'Thermal
  properties' 单独成段无 level）；ch2/ch10 章题被 'Introduction' 顶替；
  锚定成功的章也只有裸文本、章号丢失。
- **根因链**（三层，均在泛名书签位置锚定的候选规则）：
  1. 章题常被引擎标成 header（页顶大字与页眉同位，'Amorphous structure'）
     或投票缺失 level=0（'Electrons: the free electron model'）——旧候选
     只认 title+level>0，全书 1/3 的章题不在候选集；
  2. 阅读顺序兜底取页内首个文本块 → 选中复发型小节头 'Introduction'；
  3. 泛名条目（'Chapter N'）无真实标题文字可富化 → 裸章题无章号。
- **修补**：
  - 候选扩列 title/header/text + **页顶优先**（bbox y1 最小者；章扉页
    不挂前章运行头，页顶即章题）；纯文本块晋升加 ≤45% 页高闸（bbox
    缺失不闸——无位置信息时保留阅读顺序兜底）；
  - 本章小节排除：'Chapter N' 条目拒绝 'N.M' 开头的候选块（防节冒充章）；
  - 记录 `_pos_num`，stage3 显示层补章号（display-only，译文同样前置）。
- **回归**：tests/test_structure_rescue.py 扩至 59 例全绿；既有套件全绿。
  CM 真跑：ch9–14 全部带号归位（'9. Liquid dynamics'、'10. Crystal
  vibrations'、'11. Thermal properties'、'12. Electrons: the free
  electron model'…），节正确嵌套 L3；FG 38/38、刘擎全绿、
  QFT 133/133 护栏无回归。
- **状态**：已修复并验证（sidecar v1.3.8 已部署）。

## 病例 025｜汉语语义学 / 系统层 — 思考恒开模型 400 空转致死（SageRead 转换超时）

- **现象**（2026-09-09 用户报告）：图书转换超时，进度长期停在「跨页段落
  拼接」。SageRead 侧 10 分钟绝对看门狗到点杀进程。
- **根因链**：
  1. 辅助模型换成思考恒开的 glm-5.3-flash（智谱端点）；
  2. stage2/stage4 全部 5 处调用硬编码 `thinking:{type:disabled}` →
     GLM 400（code 1210「该模型始终思考，不支持关闭思考」）；
  3. `_deepseek_generate` 把参数错误当普通失败走重试退避（10/20/30s），
     每分块白烧 ~60s；本书 123 分块 / 4 并发 ≈ 31 分钟纯失败空转；
     失败也推进 done 计数 → 界面看似在干活（「LLM 空转」观感来源）；
  4. SageRead 绝对 10 分钟看门狗不随进度续命 → 健康进程被拦死。
     （MinerU 4 分钟正常；拼接候选筛选本地 0s——规则部分从来不慢。）
- **修补**：
  - `llm_thinking.py`（新）：`chat_create` 包装——能关则关（disabled），
    端点拒思考参数（400 且报文含 1210/thinking/思考）进程级降
    `reasoning_effort=low`（对齐 SageRead reasoning-map.ts 恒思考模型
    取最低档的既有口径），仍拒则不下发。不拦任何模型（用户拍板：
    恒思考模型「慢但可用」）。并发守卫：旧模式请求的迟到拒绝只按
    新模式重发、不再降档（否则首波 4 个 disabled 请求的 400 会把
    模式一路砸到 none，丢掉 effort=low 提速——实测发现）。
  - 5 处调用点改走 chat_create：stage2_hybrid ×1、stage2_common ×2
    （轻量兜底/全局定级）、stage4_translate ×2（批翻译/书名）。
  - SageRead 侧（另一仓库）：book-convert / paper-parse 看门狗改心跳式
    （实质进度静默 20 分钟才判死；sidecar 节拍爬行不续命）。
- **回归**：tests/test_llm_thinking.py 7 例（disabled 直通 / 1210 降档 /
  再拒降 none / 非思考 400 上抛 / 500 上抛 / 模式用尽上抛 / 在飞竞态不
  双降档）；既有 5 套件全绿（59+44+27+12+7）。
  真书回归（汉语语义学 447p，复用 MinerU 缓存）：
  - glm-5.3-flash（effort 协商→low）：stage2 **112s**，EPUB 正常产出；
  - deepseek-v4-flash（disabled 直通，对照）：stage2 **72.5s**；
  - 两版 nav 结构 ~95% 一致（差异为个别小节层级与扉页标注措辞）；
  - QC 红（anchor_hit 1/337、336 条目录未锚）两模型**同构复现**——
    本书 PDF 书签是垃圾形态（'100'/'101' 数字条），两跑 toc_entries
    恒 436 条（outline 先验注入，确定性来源），属本书特有的先验毒化，
    与本病例无关；形状栈+编号先验兜出了可用的树。先验毒化治理另案。
- **实测对照**（glm-5.3-flash 单分块）：disabled → 400；effort=low →
  10.6s / 16 output tokens；默认（高强度思考）→ 36s / 3322 tokens。
- **状态**：已修复并验证（sidecar v1.3.9 已部署）。

## 病例 026｜挂账立项（2026-09-09）— contd 高置信本地直裁，LLM 只裁灰色地带（风险高，暂缓实施）

- **背景**：病例 025 复盘时确认「跨页段落拼接」的 LLM 终审是 stage2 耗时大头
  （汉语语义学 447p：838 候选对 → 39 分块，占四项推理近 1/3 调用量；
  恒思考模型下单分块 10–36s）。filter_contd/merge_rules（popo/inference.py）
  本地筛候选已是零成本，LLM 只对候选对回答「拼/不拼」。
- **提案**：给 merge_rules 加双向高置信直裁——
  - 直通拼：上段以逗号/顿号/分号收尾 + 下段小写或中文承接词起首等强信号
    → 不问 LLM 直接判拼；
  - 直通拒：页码跳变 + 下块 bbox y1 回页顶（页眉误捕）、上段已是完整句
    但被换行噪声截断的形态 → 直接判不拼；
  - 只有中间灰色地带送 LLM。粗估 838 条候选 60–70% 可本地定案，
    stage2 任务 1/4 调用量同比例下降。
- **风险（铁律 0 约束）**：直通规则的失败方向必须是「不做」——
  宁可漏裁留给 LLM，不可误拼/误拆。误拼合书页眉/脚注进正文是
  病例 001–024 反复验证过的重灾区。
- **状态**：挂账，未实施。实施时必须：正反例进 tests/test_stage3_merge.py
  级套件；真书回归至少覆盖 有印刷目录书 + 无目录书 + PDF 书签书 + 中文书；
  产物亲读 nav 与章首。

## 病例 027｜汉语语义学 / stage2 — 页码书签洪水毒化 outline 先验：章/节平铺 + 章首首节被吞

- **现象**（2026-09-09 用户亲读产物）：章与节 level 平铺（全部挤在
  前言>L2）；第五章第一节、第六章第一节、第四章第三节在 nav 里消失。
  本书有清晰的数字标号印刷目录，本应万无一失。
- **根因链**（两个独立病灶，一个毒源）：
  1. **毒源**：本书 PDF 书签是扫描本第三方自制的"页码书签"——436 条里
     432 条是纯数字（'1'/'2'/…/'101'，每页一条）。`_sanitize_pdf_toc` 只拒
     '标题／页码'形态与 ≥6 位纯数字长串（ISBN），短数字串全数漏网；
  2. **平铺**：毒化先验里唯一的真文字条目'前言'（page 4）经位置锚定锁成
     唯一 L1 锚点，`_sink_unanchored_plain` 把 179 个无编号标题（含全部
     章题）压到 前言>平铺 L2；
  3. **首节被吞**：章扉页上紧跟章题的节标题被 MinerU 打 `text_level=None`
     （同页后续节标题正常打 2）→ 源头 type=text，LLM 标题判定候选
     （filter_title 只收 title 型）永远看不到它——三处被吞节全是这个形态。
     它们本可由页码救援晋升，但目录真值已被毒源换掉，救援无的放矢。
- **修补**（stage2_common.py `_sanitize_pdf_toc`）：
  - 纯数字条目任何长度一律拒收（目录里不存在纯数字真标题）；
  - ≥5 条且 ≥80% 纯数字 → 判页码书签洪水，整体丢弃 outline 先验，
    回退 LLM 目录提取（与无书签书同路径，失败方向不更差）。
- **回归**：tests/test_stage2_toc.py 新增 3 例（洪水整体丢弃 / 零散数字
  逐条剥除 / 好书签原样保留），套件 47/47；其余 4 套件全绿。
  真书（汉语语义学 447p，GLM 端）：
  - outline 洪水被拒（432/436）→ LLM 提取印刷目录 71 条 →
    锚定 70/70 全命中（修复前 1/337），标题救援晋升 24 个漏判标题——
    三个被吞节全部归位，章/节/小节正确嵌套，第四章正确从第三节起；
    QC 无红（修复前 2 红）。
  - 防回归（同代码，GLM 端，--skip-mineru 缓存复用）：QFT 109/110
    （唯一缺的是病例 024 挂账的公式节标题）、Born a Crime 24/28
    （好书签原样保留，4 缺为既有的超长章题/扉页条目）、
    Condensed Matter 29/30（缺 'Front' 垃圾书签条目）——均无新增退化。
- **状态**：已修复并验证（sidecar v1.3.9 已部署）。

## 病例 028｜结构真值来源重订（用户拍板 2026-09-09）：outline 先验弃用 + 前后页词表锚定

- **决议**：PDF 自带书签不再作为目录先验（形态不可控——假目录 019 /
  泛名条目 022 / 页码书签洪水 027 三连事故），只信 OCR 重建目录；
  前言/序言/序/序章/前记/作者简介/版权页（及英文 foreword/preface/
  prologue/copyright/dedication/acknowledgments/about the author 等）
  这类每本书都有、印刷目录常不收的条目改由词表规则锚定。
- **修补**：
  - `pipeline.py`：停止读取/注入 pdf_toc（`_read_pdf_outline` 保留备查，
    恢复须先过 `_sanitize_pdf_toc`）；`toc_source` 恒 None，
    stage3 泛名书签正文起点回收随之闲置；
  - `stage2_common._anchor_frontmatter_lexicon`（新）：归一化全等或
    '词表词+括号署名'（'前言（贾彦德）'）+ 页区约束（首章前/末章后，
    无章号书回退 前25页/80%处）→ 锁 L1；text 块晋升为 title。
    词表刻意保守（导言/Introduction/附录/参考文献/目录不收）；
    **目录页跳过 + 同页 ≥2 命中整页跳过**（BAC 实测：Contents 页罗列行
    Dedication/Acknowledgments/About the Author 被锚出幻影前页条目）；
  - `stage2_common._calibrate_levels` 标题救援：64 字符长度上限只挡
    非标题块——引擎已标 title 的多行超长章题（BAC ch12，归一化 105
    字符）豁免（与位置闸门"引擎已标标题不套用"同口径）；
  - `stage3_epub` divider 判重补同 kind 约束：章标题以 'Part III: The
    Dance' 收尾时，裸分区词 'PART III' 被包含判重误并进该章（章被改名、
    分区页消失——与病例 022 章分支同 kind 约束同构，divider 侧补上）。
- **回归**：test_stage2_toc 54/54（+词表正反例 4 例、救援长度正反例 2 例）、
  test_stage3_merge 29 例（+divider 同 kind 正反例 2 例）、其余 4 套件全绿。
  真书 6 本（GLM 端，--skip-mineru 缓存）：
  - 汉语语义学 71/71 全锚定、QC 无红；前言/序/版权页词表锚定 6 条；
  - BAC：PART I/II/III 平级正确嵌套，ch10/12/14 三章题全部归位
    （ch12 靠救援豁免、ch14 靠 divider 同 kind 修），无幻影条目；
  - CM 228/229、QFT 138/140（两缺均为公式/前缀形态目录条目，QC 红但
    树内标题存在——病例 024 挂账的公式归一化专题，内容未丢）；
  - GI / 伊豆（无目录书，纯形状栈路径）不受影响，产物同前。
- **已知挂账**（随 outline 弃用新生）：born-digital 且无印刷目录页的书
  （好书签是唯一结构来源）退回形状栈路径，层级可能变粗；真遇到再议。
- **状态**：已修复并验证（sidecar v1.3.9 已部署）。

## 病例 024 追记（2026-09-10 修复）：公式标题归一化 + 标签前缀后缀匹配

- **残留问题**（024 挂账）：公式型节标题锚不上——'$K^{0}\rightarrow\pi^{-}l^{+}\nu_{l}$'
  等 LaTeX 公式串与目录 OCR 形态差异大；另有目录条目省略 'Complement: '
  标签前缀的形态（'Isospin and flavor SU(3)'）。
- **修补**（stage2_common.py）：
  1. `_normalize_title` 加 LaTeX 归一（`_LATEX_SYMBOL_MAP` 希腊字母/运算符
     → Unicode、去 `{}`/`^`/`_`、未收录命令保留字母主体）——两侧同归一，
     非公式文本不受影响（'$W^{\pm}$' ↔ 'W±'）；
  2. `_match_anchor` 新增「锚点是块的尾部」规则：块多一个冒号标签前缀时
     命中（冒号限定防过匹配——裸后缀会把 '绪论' 错配到 '附录：绪论'）。
- **回归**：test_stage2_toc 57/57（+3 例）；QFT 真跑 anchor_hit
  **112/112 全绿**（修复前 138/140，两缺全部锚上）。
- **状态**：已修复并验证（sidecar v1.3.9）。

## 病例 029｜八次危机 / stage2 轻量兜底 — 目录被前页推出采样窗 → 锚点体系全灭

- **现象**（扩测试集·用户供书）：转换完成但结构稀碎——QC 红「检测到目录页
  但无目录条目」，章/节靠首票平铺。
- **根因**：本书推荐序+自序+概念提示把目录推到扫描页 P23-31，而轻量兜底
  只采样书首 `_FRONT_PAGES=15` 页——LLM 按 prompt 规则（采样中无目录页
  必须输出 []）正确输出了空数组。两个子系统口径不一致：目录页识别器
  （条目行启发式）看得到 P23，采样器看不到。
- **修补**（stage2_common.py）：`_find_toc_page`（新，本地探测独立
  '目录/目錄/Contents' 短行页）+ `_light_metadata_pass` 采样扩窗——
  目录页超出前 15 页时扩窗覆盖到 目录页+8（跨页目录续页也在窗内）。
- **回归**：test_stage2_toc 正反例；八次危机真跑 toc_entries 0→47、
  锚定 41/46，nav 恢复 部分→章→节 正确嵌套。

## 病例 030｜中國36問 / stage2 轻量兜底 — GLM 内容过滤（1301）整单拒答

- **现象**：同上红字「目录页但无目录条目」。目录页（P7-9）就在采样窗内。
- **根因**：GLM 端点 contentFilter 1301——该书的目录条目本身就是敏感
  词表（'中国共产党政权是否具有合法性'/'台湾统一'/'毛泽东'），整单 400，
  拆分紧凑重试同样被拦。端点侧不可绕（也不应绕）。
- **修补**（stage2_common.py `_rule_toc_extract`，新）：LLM 交付 0 条且
  本地探到目录页时，从目录页规则提取——条目尾页码（前导零 001/点线
  剥除）、作者行页码归并（'裴宜理 (Elizabeth J. Perry) 13' 的页码归
  上一行条目）、行内作者剥除（'导论 宋怡明 (…)' → '导论'）、介绍性
  长段落滤除（>60 字符不收）、无页码结构词条目（第X部分/序导言）以
  page=None 收录、形状定级首票（部分/章/N./一、/（一）阶梯）。
  只在 LLM 空手时启动；提取为空则维持现状（失败方向=不动作）。
- **回归**：正反例 3 例进 test_stage2_toc（含作者行归并与 noop）；
  中國36問真跑 toc_entries 0→44、锚定 38/39（唯一缺是一条断行残片
  '德怀'——作者名在文本层中间断行，挂账）；nav 恢复 导论/部分→问 嵌套。
- **残留**：GLM 的内容过滤对政治敏感书目是系统性风险——规则兜底只覆盖
  目录提取，stage2 标题判定分块若命中过滤则该分块首票缺失（形状栈兜底）。
  根治路径是换端点或按 1301 标记自动降级到备用模型，另案再议。

## 病例 031｜全球化与国家竞争 / stage2 锚定 — 文字版 PDF 的 HTML 工件毒化标题键

- **现象**：文字版 PDF（无页码的整理版，极限压测样本）章题锚不上：
  '第二章 土耳其<sub>：</sub>地缘格局重构中的"土耳其模式"'。
- **根因**：文本层把冒号排成下标 HTML（<sub>：</sub>），归一化键带着
  标签残渣，与目录条目（'第二章 土耳其：…'）失配。
- **修补**（stage2_common.py `_normalize_title`）：归一化剥 HTML 标签
  （`<[^>]+>`）——两侧同归一，非标签文本不受影响。
- **回归**：test_stage2_toc 61/61（+1 例）；本书真跑验证见 git log。
- **状态**：全部已修复并验证（sidecar v1.3.9）。

## 病例 032｜民法总论 / VLM 引擎 × stage3 — 裸圈码脚注永不锚定（isalnum 陷阱）

- **现象**：VLM 引擎真书回归，EPUB 章末尾注正常渲染（内容不丢），但
  正文 noteref 链接 0 条（MinerU 版同书 1736 条）。
- **根因链**：stage3 `claim_footnotes` 的裸匹配守卫写作"非字母数字的圈码
  才退化裸匹配"，实现用 `not mark[0].isalnum()`——但 ① 的 Unicode 类别是
  数字（No），`isalnum()` 判 **True** → 裸圈码永远进不了裸匹配分支。
  MinerU 内容因正文圈码走 `$^{①}$` 上标约定（`_convert_latex_sup` →
  `<sup>①</sup>`），从不触发该分支，bug 潜伏多年未暴露；VLM 引擎正文
  直接产裸圈码（①），全灭。
- **修补**（双管齐下）：
  - stage3_epub.py `claim_footnotes`：守卫改
    `not (mark[0].isascii() and mark[0].isalnum())`——圈码等 Unicode 数字
    可以裸匹配；ASCII 字母/数字仍只认上标（原意不变，防误伤正文）。
  - stage1_vlm.py `_wrap_page_markers`（适配器层）：正文圈码中**本页确有
    对应脚注**的包成 `$^{①}$` 契约约定形态；无对应注文的圈码保持裸字
    （内联列举场景，失败方向=不动作）。
- **回归**：新测试 tests/test_stage3_footnote.py（裸圈码锚定 / ASCII 数字
  不裸匹配 / $^{①}$ 不破）+ tests/test_stage1_vlm.py 加包标用例；
  全链 8 个测试文件绿；民法总论重出 EPUB：noteref 952 / 尾注 476
  （超 MinerU 版的 434），QC 红牌 0。
- **状态**：已修复并验证。

## 病例 033｜民法总论 / VLM 引擎 × stage3 — 同页新开章/节冲掉脚注锚定

- **现象**：用户亲读 EPUB 发现大量脚注只进章末尾注、正文无 noteref 链接
  ——集中在"页中新开章/节"的位置。
- **根因链**：`flush_footnotes` 在每个标题块处按 `page ≤ last_page` 冲走
  全部未锚定脚注；同页脚注的锚点标记在本页后段的正文块里才出现，标题
  先把脚注冲走（标记 claimed）→ 后段正文处理时锚定失败。MinerU 引擎同
  理存在（874+ 脚注仅 434 锚定的部分根因）。
- **修补**（stage3_epub.py `flush_footnotes(before_page)`）：标题处只冲
  该页**之前**的脚注，同页脚注留给本页后段锚定；全书收尾的 final flush
  仍全部冲走（内容永不丢）。
- **同案修补**（stage1_vlm.py `_demote_running_heads`，用户同读发现的
  页眉泄漏）：GLM 偶发把书眉塞进正文块（'012 民法总论'），污染正文且
  截断跨页合并；页首"数字页码+书眉候选"短块降级为 header（候选=高频
  running_head ∪ 目录 L1/L2 条目；无页码章题豁免，失败方向=不动作）。
- **回归**：tests/test_stage3_footnote 新增同页标题不提前冲脚注例；
  tests/test_stage1_vlm 新增页眉降级 5 例；全链 9 文件绿；
  民法总论重出 EPUB 验证（见 NOTES）。
- **状态**：已修复并验证。

## 病例 034｜QFT / VLM 引擎 × 目录区检测 — 章首 mini-TOC 吞掉 ch1-3（含挂账）

- **现象**：QFT（物理书，每章章首带 mini-TOC）VLM 全程，EPUB 的 nav 里
  第 1-3 章凭空消失，第 7/8 章节标题带 "… 180" 页码残渣。
- **根因链**：模型把章首 mini-TOC 行判为 toc 块 → 引擎按目录契约形态
  （条目+尾页码）输出 → stage2 目录区检测把目录区判定为 p6-59（含章首
  mini-TOC）→ front_matter toc keep=false → body_range 从 p60 开始 →
  ch1-3 标题与正文被整段排除（EPUB 静默丢 43 页）。
- **修补**（stage1_vlm.py，适配器层，只动文本形态）：
  - `_toc_region_pages`：真目录区 = ≥3 toc 块的连续页组且条目最多（≥5 条）；
    区域内 toc 块保留 MinerU 契约形态；区域外（章首 mini-TOC/误判）降级
    为普通正文并剥点线页码（`_strip_page_suffix`，同样用于标题块）。
- **回归**：tests/test_stage1_vlm 新增区域判定/降级 e2e 共 3 例；全链 9
  文件绿；QFT 重产：nav 恢复 ch1-3 与全书嵌套、页码残渣清零、anchor
  112/112、missing_real 0；民法总论区域判定不受影响（简目+详目连续组）。
- **状态**：已修复并验证。

### 挂账：'12 Solutions to exercises' 幻影章（stage2 锚点尾匹配，两引擎共性）

- **现象**：QFT nav 在 ch1 与 ch2 之间多出顶层章 '12 Solutions to exercises'。
- **根因链（已定位）**：ch1 末尾的合法小节标题块 'Exercises' 命中锚点匹配
  规则 3（块是锚点尾部，分隔页模式）→ 被锚点富化改写为
  '12 Solutions to exercises' 并锁 L1（锚定即锁死，后续流程全部豁免）；
  位置闸门因"引擎已标标题豁免"未拦（VLM 把 Exercises 标为 text_level 标题）。
  真章 '12 Solutions to exercises'（p282，header 首现）未被晋升，幻影独占。
- **影响面**：锚点规则 3 的尾部匹配对通用单字/单词尾过宽——'Exercises'、
  'Summary' 类通用小节名在任何含 'X … to exercises/summary' 目录条目的书里
  都可能被尾配。MinerU 引擎同病（本例未显形纯属运气）。
- **修补建议（待用户批准，属锚点核心语义改动）**：规则 3 加窄守卫——
  块为锚点尾部时，要求块长 ≥8 字符或 ≥2 词，或要求块同时是锚点的
  ≥40% 覆盖（与规则 5 同口径）；回归须覆盖 BAC/必须保卫社会分隔页场景。
- **状态**：挂账待决。

## 病例 034 追记（同日深夜第二轮，QFT 用户亲读反馈）

- **公式纪律**（灾难级，用户判定）：VLM 引擎 prompt 从未约束公式形态 →
  模型把公式写成 Unicode 平铺（√ 而非 \sqrt{}），全文仅 187 个 <math>
  （规则引擎版 7416）。修补：_PAGE_PROMPT 加铁律 7（行内 $…$、display
  $$…$$ + \qquad（编号）、禁 Unicode 平铺）。重读后 <math> 8576、display
  block 1493，**反超规则版**；阅读器实拍确认 display 公式居中+编号右置。
- **mini-TOC 丢弃守卫修正**：④"下游有真标题"初版只认更后**页**，QFT 实态
  是章首页 mini-TOC 与真节标题同页并存 → 条件改为更后页**或本页更后块位**；
  且**只丢 toc 型行，title 型永不丢**（防真标题被自身副本证据误杀）。
- **裸编号章题合并**（_merge_bare_number_titles）：模型把章题拆成
  '7' + 'Quantum electrodynamics' 两块，裸 '7' 成幻影孤儿空章 → 页内紧邻
  双 title 块合并（仅 ^\d{1,3}$/^第…[编章]$ 形态，余者不动作）。
- **回归**：test_stage1_vlm 17 例（新增丢弃同页形态/合并守卫/拆分归一）；
  QFT 重发 QC **全绿**（空章 6→0、孤儿 0、锚 110/110、missing 0）。
- **残留挂账（小）**：'3.1 The action principle where L is called…' 行内
  连排一处——页眉首现块被 stage2 锚点晋升为 text 后与正文段合并（规则层
  既有行为，MinerU 同构）；Fig 5.3 占位符与图 5.4 同页并存（配对边界）。
  幻影章（尾匹配）挂账维持待决。

## 病例 034 第二追记（幻影修复落地 + 公式清洗链 + bbox 回退 + 书眉守卫）

- **幻影章修复（用户批准）**：`_match_anchor` 通用小节名词表守卫
  （`_is_generic_tail_word`：exercises/summary/introduction…）同时罩住
  尾部匹配（规则 3）与子串匹配（规则 5）——实测幻影走的是规则 5
  （9/22=40.9% 刚踩过 40% 覆盖闸），初修只罩规则 3 无效，双路后幻影消失、
  真 ch12 成形（12.1-12.5 小节齐全）。回归 test_stage2_toc 62/62
  （含 RUN/权利主体 等专名尾部匹配保留例）。
- **公式清洗链（stage3_epub，两引擎共享受益）**：
  `_strip_alignment_amp`（剥对齐 &——'\&'换行+对位同样要剥，保护符只罩
  前非反斜杠的 \&；latex2mathml 会把对齐 & 渲染成可见字符，5.46 实测）；
  `_sanitize_latex`（模型笔误窄守卫：\qqud→\qquad；单参数命令粘连字母
  补花括号 \slashedp→\slashed{p}，\hbar 等固有命令不误伤）。
  回归 test_stage3_footnote（剥 & 四例 + 笔误五例）。
- **游离公式编号归位（stage1_vlm `_fix_dangling_eq_numbers`）**：
  '\qqud(3.47)' 裸源码/块首裸编号紧贴下文 → 并入 $$ 内（\qquad 规范间距）；
  编号限定 (章.序号) 带点形式，'(1) 第一点' 列表标记不吃。
- **bbox 定位回退链**：doubao 对个别页输出裸数组/絮语（want_json 三次
  解析失败）→ `_detect_boxes` 宽容解析（{"images":[]}/裸数组/单框），
  主定位失败自动回退转写模型（glm 同页可解）——Fig 5.3 掉图修复。
- **书眉混正文守卫（`_head_is_running`）**：书眉==当前章题且 printed_page
  已深入章节内部（> 条目印刷页）→ 不再发射 header 块（防 popo 首现规则
  复活进正文再被锚点晋升，'3.1 … where L…' 连排消失）；章首页书眉
  （≤ 条目页，病例 004 晋升通道）与页码缺失一律保留。
- **全链 9 测试文件绿；QFT 终验：幻影 0、可见 & 0、\qqud 0、slashedp 0、
  连排 0、Fig 5.3/5.5/5.6 图齐；QC 全绿。**

## 病例 035｜Feeling Great / VLM 引擎 — 表格结构全失（markdown pipe + table_body 契约）

- **现象**：表格书真书回归，EPUB 的 HTML <table> 数为 0——quiz 表格
  （Part 1: Your Moods 等 195 处，MinerU 真值）全被逐行摊平成文本。
- **根因链**：生产 prompt 未定义表格输出形态（"表格文字尽量按行转写"是
  实验版口径残留）；且契约字段 mismatch——popo/convert.py 读
  `table_body`，初版发射 `text` 字段导致二次归零（第一修仍 0 张）。
- **修补**（stage1_vlm.py）：
  - prompt 增表格纪律：`{"t":"table","caption":…,"text":"markdown pipe
    表格（| 列 |…，首行表头、次行 |---| 分隔）"}`；
  - `_table_md_to_html`：pipe → 良构 HTML（单元格全转义、列数自动补齐），
    解析失败回退普通文本块（内容不丢）；
  - 契约发射 `{"type":"table","table_body":html}`（对齐 MinerU 字段）。
- **回归**：test_stage1_vlm 新增 pipe 转换 4 例；FG 表格页重读 146 页
  （按 MinerU 真值页表定点删页重读，断点续跑机制复用）后 EPUB
  <table>=164；抽查 Part 1 quiz 表 thead/tbody 结构完整。
- **状态**：已修复并验证。残留黄牌（orphan 48/空章 21/重复 3）为
  Feeling Great 章级目录固有形态，挂账观察。

## 病例 036｜Markdown/TeX 平行导出器（stage3_export.py）— 新功能与四个实测坑

- **功能**：`--format epub,md,tex` 多选导出（GUI 设置页同义勾选）。
  **不走"从 MathML 反推"**：复用 `_render_popo_body` 的单元判定
  （切章/段落合并/脚注锚定原样继承），从其 HTML parts 的
  `<math alttext>` 回收 LaTeX 源码；display/行内判别与 EPUB 同函数
  （promote_lone_display_math，病例 015 教训）。图片复制 images/ +
  相对链接；表格 HTML→pipe（md）/longtable（tex）。
- **选项全留用户**：md 单文件/分章（--md-split）、方言 gfm/pandoc、
  tex 完整文档（xelatex+ctexbook/book，可直接编译）/片段、
  语言 auto/orig/trans/both（双出加 "_原文" 后缀）。
- **实测坑（全部带回归测试）**：
  1. 内联 HTML 的 math 被 get_text() 吞掉 → 占位符令牌（\x00MATHI/B、
     FNREF）贯穿转义层，发射器在各自转义**之后**再替换（否则 tex_escape
     摧毁 LaTeX 源码/footnote 命令）；
  2. 章内 h2 与单元标题重复 → 归一化比对去重；
  3. 未锚定尾注（aside 无 id）不得产 `[^]:` 空标（GFM 不渲染无引用定义）
     → 退化显式"注释"列表（QFT 90 处上标尾注实测）；
  4. Windows 文件名冒号进 ADS 流（'Feeling Great: …' 写丢）→
     `_safe_stem` 文件名安全化。
- **回归**：tests/test_stage3_export.py 10 例；真书导出抽查：民法总论
  md 脚注 896 引/900 定义；QFT md 1475 display+6680 行内公式存活、
  61 图链接有效；FG 164 表全转（md 1303 pipe 行 / tex longtable）。
- **状态**：完成。CLI/GUI/run_batch/spec 四线接线完毕。

## 病例 037｜自迭代批一（必须保卫社会 + 高等数学）— 引擎转义修补 / 日期锚点 / 目录区定位三连

- **高等数学红牌：'第二节 定积分在几何学上的应用' 未锚上**。根因链：
  模型在含 LaTeX 的页面上产出的 JSON 带孤反斜杠（'\sqrt' 的 \s 是非法
  JSON 转义）→ extract_json 解析失败，三次重试同死 → 页 27/290 整页失败
  → 标题丢失。修补（vlm_client.py extract_json）：先精确解析，失败修补
  孤反斜杠再解析（仍失败才 None，不动作）。QC 红牌正是靠它发现缺页——
  目录未锚定=页丢失的可靠探针。
- **必须保卫社会 nav 只剩前两讲**。根因链（三层，自迭代最硬的一根）：
  1. stage2 伪造目录硬兜底丢弃 LLM 提取的 16 条——检测器的点线引导行
     判据要求 ≥2 点字符（'…… 003'），引擎初版后缀用单省略号 '… 9' →
     不识别 → 判定无目录页。修补（适配器层）：toc 条目后缀改 '…… N'
     （MinerU 形态）。
  2. 修 1 后检测仍败：目录区判定 `_toc_region_pages` "取条目最多组"
     被书末索引页（331-332，比真目录 3-5 更稠密）偷走。修补：定位优先级
     改"目录/CONTENTS 页首词的组 > 前 50% 最早组"，后半部稠密组排除。
  3. 锚点仍 0：正文标题是纯日期（'7 JANUARY 1976'），目录条目是
     日期+整段摘要——既有匹配规则全部不命中（覆盖率 20%<40%）。
     修补（stage2_common `_match_anchor` 新窄规则，用户自迭代授权）：
     日期强键——纯日期形块（^\d{1,2}[a-z]+\d{4}$）匹配含同日期串的条目；
     多同日期条目看剥日期后余部：同则取最短（简目/详目重复），不同则
     明确不动作（不落模糊兜底）。
- **回归**：test_stage1_vlm +转义修补例、test_stage2_toc +日期键 4 例
  （RUN/权利主体保留例在幻影守卫处已验）；must_defend 重跑：one–eleven
  十一讲全部锚定归位（anchor 16/16，missing 0）；gaoshu 重跑：红牌消失
  （第二节归位），56/56 锚定。残留黄牌：长摘要章题显示冗长（nav 条目=
  目录全文，显示层）、引号扉页空章 1 个——均挂账观察。
- **状态**：已修复并验证。

## 病例 038｜自迭代批二（汉语语义学 + 伊豆の踊子）— 括号归一 / QC 空章口径 / 日文难页挂账

- **汉语语义学**（初跑 2 未锚 + 2 幻影 + 1 无目录 + 3 空章 → 终跑全绿）：
  1. **全半角括号失配**（根因）：目录条目半角 '（义位系统）' ↔ 正文全角
     '（义位系统）' 不归一 → '第七章 语义场（义位系统）（下）' 锚不上 →
     页码救援在偏移可信处合成幻影。修补：`_normalize_title` 加全半角括号
     归一（单行词形窄修）。'第九章 句义（下）' 幻影同步消失。
  2. p161/p280 整页失败（同 037 的 JSON 孤反斜杠，转义修补后回填）。
  3. **QC 空章误报**：popo 树 image 节点的 level 字段装的是块 id
     （popo/tree.py:143 `level=element['image']` 关联语义），QC 把
     level>0 的 image 节点当空章 → 3 个 'image' 空章。修补（qc_book.py
     显示层）：空章统计跳过 image/table/chart/seal/image_block 类型节点。
  4. 终跑：anchor 71/71、synth 0、空章 0、missing 0；残留 orphan 107
     黄牌为章级目录固有形态（不丢内容）。
- **伊豆の踊子**（老日文竖排+振假名+合集结构，黄牌 1 空章 + 红牌 3 未锚）：
  - '供養と赤屋敷'（目录页）vs '牧場と赤屋敷'（正文）：亲读图像判决——
    目录页与正文**都是'牧場と赤屋敷'**（牧场的牧读错成供养的供）——
    模型在目录页单字误读，正文页读对。**自愈机制没有自愈**（两处独立阅读
    不一致）。挂账：引擎侧"目录先验 ↔ 正文标题"一致性交叉校验（差异 ≥
    阈值时重读该页）值得做，先登记。
  - 'ひらかめ'（目录误读'ひらかぬ門'）同类。
  - '解説'（目录简写）vs '作者と作品について（解説）'（正文全称）：
    **锚点⊆块方向无规则**（既有规则只覆盖 块⊆锚点）——新匹配方向，
    属锚点语义改动，**挂账待用户批准**。
  - 空章 '乙女の港'：p85 同页两个 L1 标题（乙女の港 + 花えらび）相邻，
    前者空单元内容被后者吞（nav 层级语义挂账）。内容未丢（missing 0）。
  - 与规则引擎版对照：互有胜负（规则版丢 花えらび/ひらかぬ門/銀色の校門
    更多条目；VLM 版条目更全但本章三处红牌）——老日文难页是两引擎共同天花板。
- **状态**：hanyu 已修复并验证；izuno 挂账（详见上）。

## 病例 039｜图片定位独立模型（豆包）选项移除 — 产品决策（非缺陷）

- **决策**（2026-09-16 用户拍板）："专门为了图片而引入豆包太蠢了，直接
  消灭这个选项"。产品管线图片定位恒为 **转写同模型粗框 + raster_snap
  光栅重裁**（零额外 key，T4b meanIoU 0.883 / IoU≥0.8 比例 95.2%）。
- **移除点**：config.py `VLM_BBOX_*` 三行；stage1_vlm.py 独立 bbox 客户端
  分支及"主定位失败回退转写模型"死代码（同源后恒不触发）；.env.example /
  .env 的 `VLM_BBOX_*`；scripts/vlm_lab/engine_smoke.py `--no-doubao` 旗标。
  vlm_client.py 火山思考档映射保留（通用基础设施）；scripts/vlm_lab/* 实验
  脚本与 T4（doubao 0.976）成绩保留为实验档案。
- **回归**：全量单测 11 文件绿；真实页段冒烟（minfa p19–35，glm 单模型
  定位）：16 页 0 失败，图片 p0020_0/p0033_0 与原基线一致抽出。
- **状态**：完成。wiki/01、wiki/06 §关键结论 1 已同步决策记录。

## 病例 040｜完成提示音卡顿/截断 — winsound.Beep 软件蜂鸣根因

- **现象**：转换完成提示音（上行琶音）有时卡顿、有时只播一部分。
- **根因**：`winsound.Beep` 是上古蜂鸣器 API，现代 Windows 走软件模拟且
  **同步阻塞**发声；转换收尾时进程正忙于导出打包（CPU 高峰），模拟蜂鸣
  即卡顿截断。发声点两处：pipeline.py 末尾、app.py `_play_done_sound`。
- **修补**：新模块 `completion_sound.py`——`winsound.PlaySound(wav,
  SND_ASYNC|SND_NODEFAULT)` 异步播放真实音频（零新依赖）；默认音效
  `assets/complete.wav`（Kenney Interface Sounds `confirmation_002`，CC0，
  用户亲选；ogg 经 libsndfile 转 44.1kHz/16bit wav）。环境变量
  `CONVERT_COMPLETE_SOUND`：off=静音、自定义路径=换音。失败方向=不动作
  （任何异常静默跳过）。两处发声点均已替换。
- **回归**：全量单测 11 文件绿；默认/off/无效路径三态调用无异常。
- **状态**：完成。GUI 设置页的提示音开关+试听在新 GUI 迭代中一并接入
  （sidecar 注入 `CONVERT_COMPLETE_SOUND=off`）。
- 备注：为一次性转码向 .venv 装了 soundfile（开发工具，非运行时依赖）。

## 病例 041｜TeX 导出 43 编译错误连环修 — 实体残骸 / 表格公式扁平 / 编号叠加

- **现象**：高数（公式密集）tex 导出 xelatex 43 错、267 缺字符警告；表格内
  公式扁平成 unicode 文本；md/tex 复制到输出目录后图片断链。
- **根因链**（五层）：
  1. stage3_epub `_strip_alignment_amp` 无差别剥 `&` → HTML 实体被腰斩
     （`&#x27;`→`#x27;`、`&gt;`→`gt;`）混进 alttext 与 MathML 树——
     **EPUB 公式同样受害**（导数公式表渲染出 `#x27;` 残骸）。
  2. 脚注锚定在最终 HTML 上作业，把 `<a noteref>` 写进 math 的 alttext
     属性值 → 导出 tex 时 `#` 炸数学模式。
  3. 导出层 `_walk` 表格单元格用 `get_text()` → 单元格里的 `<math>` 被
     压成扁平 unicode（`$(x^{\mu})'=\mu x^{\mu-1}$` → `(xμ)′=μxμ−1`）。
  4. 模型把 unicode 数学符号（λ、₀、′、①）直接写进 $…$；xelatex 数学
     字体（lmroman）无字形 → 满屏 Missing character。
  5. `\lambdax` 希腊命令粘连字母（unicode 映射与模型笔误同源）；
     `\overparen`/`\xlongequal` 缺宏包；`\chapter` 自动编号与书自带编号
     叠加 → 页眉"第二章 第二章"。
- **修补点**：stage3_epub `_sanitize_math_input`（实体反转义**先于**剥 &；
  反斜杠粘连 CJK `\、`；希腊命令粘连补空格）；stage3_export `_clean_latex`
  （剥泄漏标签+实体残骸修复）、表格单元格改走内联通道（alttext 存活）、
  `_tex_math_sanitize` unicode 映射表（含圈码①-㉟/数学粗体字母/修饰上标
  长尾）、`_tex_escape` 文本模式同步扩展、preamble +extarrows+yhmath、
  标题全改星号命令+手动 addcontentsline（书自身编号为准）、
  `export_book` 共享 images/ 随产物交付、`export_markdown` 分章自含
  `<书名>_md/` 目录。
- **回归**：tests 13 导出用例+全链绿；高数 tex 编译 **43→0 错、
  缺字符 267→0**、634 页；EPUB QC anchor 56/56、formula_artifacts 0、
  页眉单编号；栅格化抽页亲读公式/表格排版正常。
- **状态**：已修复。**挂账**：stage3_epub 脚注锚定应避开 alttext 属性
  上下文（EPUB 侧 alttext 兜底字符串残留 `<a>`，MathML 显示不受影响）。

## 病例 042｜2×2 图阵 caption 配对错位 + 导出品控二轮（布局/前页/浮图/display 居中）

- **图配错位（引擎侧）**：高数 p24 的 2×2 图阵，图1-7/1-8、1-9/1-10 整列
  互换（文件内容与 caption 不符）。根因：`_extract_images` 的框排序用纯 y
  主序，同行两图 y 微差被噪声翻转。修补：`stage1_vlm._reading_order`
  行感知排序（y 聚行[垂直重叠≥50% 即同行] → 行内按 x），单测固化
  （test_stage1_vlm +2 例）。回归：115 个图片页定点失效重跑，p0024_0
  归位图1-7。**既有产物（minfa 等）同类页面可能同病，重转换即得修。**
- **产物目录布局**：散文件 → `<输出>/<书名>/<格式>/`（epub/md/tex 各自
  子目录，md/tex 自含 images/；分章 md 内容平铺进 md/）。pipeline.py
  `_deliver` 统一交付，registry 记录新路径。
- **md/tex 丢前页后页**：`build_units` 此前只取 `_body_range` 正文 →
  前言/版权页/附录全丢。修补：front_matter/back_matter 的 keep=true 条目
  各并成一个单元（label 命名）置于正文前后；封面图（work_dir/cover.jpg）
  前置进 md 首部与 tex \maketitle 后。
- **md display 公式没居中**（用户 Typora 实测）：行内嵌套的 display 数学
  以 `\n$$…$$\n` 发出，Typora 退化为行内小字甚至源码块。修补：空行环绕
  `\n\n$$…$$\n\n`（独立段落是 GFM/Typora 居中渲染的前提）。
- **tex 掉图**：[h] 浮动体漂移堆积（图1-7~1-10 全漂走）。修补：float
  宏包 + [H] 精确就位；caption 改星号形式（书自带图号，消灭"图 11:
  图 1-7"双编号）；前页带入的 □/■ 补 \square/\blacksquare 映射。
- **回归**：高数重跑后 tex 编译 0 错 0 缺字符 768 页；图1-7~1-10 全部
  就位且配对正确（栅格化亲读）；md 前页/封面/附录齐、display 空行生效。
- **状态**：已修复。**挂账（用户点名）**：VLM 表格处理深度未系统验证
  ——跨页合并、多层表头、表内公式/图片、表格与正文边界等场景需要
  自迭代优化与真书验证（Feeling Great quiz 表之外缺覆盖面）。

### 病例 042 续｜JSON 合法转义吃 LaTeX 命令字母 + 页眉幻影 + 间距归一

- **JSON 合法转义隐患（引擎根修）**：模型在 JSON 字符串里单反斜杠写
  LaTeX 时，`\f`/`\b`/`\t`/`\n`/`\r` 是**合法** JSON 转义——解析静默解码成
  控制字符吃掉命令首字母（`\frown`→`\x0crown`、`\beta`→`\x08eta`、
  `\times`→`\x09imes`），病例 037 的孤反斜杠修补管不到（它不报错）。
  高数 p161 `\overset{\frown}` 实测：1 个控制字符级联出 19 编译错误。
  修补（vlm_client.extract_json 后处理 `_restore_ctrl_escapes`）：
  **只在 $…$ 数学段内**把控制字符恢复为命令字母（散文换行/制表是合法
  空白绝不许碰），test_stage1_vlm 固化（散文 \n 不恢复例）。
- **锚定间距归一**：目录 `\cos \omega x` ↔ 正文 `\cos\omega x\,` 失配
  （λx 型节标题锚不上）：`_normalize_title` 剥 LaTeX 间距命令 `\, \; \: \!`。
- **运行页眉幻影**：`第三章 习题 3-1（第 132 页）`（章名+节名+页码后缀
  的页眉固定形状，出现在附录答案区）被晋为 L1 标题。真标题绝不带
  （第 N 页）后缀 → `_veto_junk_titles` 新增 `_JUNK_RUNNING_HEAD_RE` 降回正文。
- **sanitize 新窄规则**：`_'(.)'` 下标/撇号序颠倒（`\Phi_'+'(a)`→
  `\Phi_+'(a)`，高数 p253）；剥行尾孤反斜杠（闭合 $ 误写为 `\$`，
  高数 (7-11) 级联炸 `\[`）。
- **回归**：高数 115 图片页重跑（配对修复）+ 失败页 127/321 重试 + p161
  控制字符页重跑后：QC anchor 56/56、synth 0、missing 0、幻影清零；
  tex 编译 0 错 0 缺字符 778 页；overset/Φ/公式页栅格化亲读正常。
- **状态**：已修复。阶段教训：tex 编译是公式链路最强探针（把 EPUB 里
  静默的 alttext 污染全部显形），后续大改建议保留"编译过一遍"验收位。

## 病例 043｜VLM 专用 Stage 2（stage2_vlm）— 架构拆分（用户批准，wiki/09）

- **决策**：规则 Stage 2（stage2_hybrid）为 OCR 烂输入设计（DeepSeek 四项
  重打标 + 目录检测家族 + 页码救援/幻影合成），用在 VLM 结构化产物上
  是"弱模型改强模型判断"+救援规则纯副作用（"反向救援"误伤 VLM 正确
  标注的页眉；目录先验被晾着，037 被迫把 VLM 输出掰弯喂 OCR 检测器）。
  拆为：**stage2_hybrid 原样留给规则引擎；新 stage2_vlm 薄编排器**
  （~540 行，设计稿 wiki/09），共享 stage2_common 函数库（归一/匹配器/
  形状栈/降格守卫——不复制，避免未来 bug 翻倍）。
- **编排**（契约偏差与裁定，均已在交付摘要确认）：
  - 裁：DeepSeek 四项标注、目录检测家族、`_rescue_by_page`（核心）、
    `_global_level_pass`（换规则版局部形状栈）、`_anchor_generic_outline`；
    保：`_light_metadata_pass`（全书唯一 LLM 调用点）、锚定校正、
    降格三守卫、词表锚定、build_tree。
  - stage1 图文配对直连（image=caption 块 id），规则版 contd
    （prev.contd=next.id 工作约定），表格合并复用 popo util；
    目录先验（vlm_state.db toc_entries）直接作锚；db 缺失回退 light 提取。
  - **stage3_epub.py 两处 engine 门禁**（1512/1632）扩展 `"vlm-hybrid"`
    ——"下游零感知"预设在精确匹配上不成立，最小扩展追认（造假写
    engine="hybrid" 是更脏的选项）。
  - 新增 `structure["cross_check"]` 三列表（未锚/层级不符/页偏移异常），
    只报告不动作——二期升级为"差异重读该页"（伊豆挂账的正主）。
- **minfa 冒烟**（--skip-mineru 复缓存）：anchor 234/234 全中、synth 0、
  missing 0、空章 0（首轮 16 → 局部形状栈根修）、脚注 902 全保、
  cross_check 三列表全空、orphan 231→217 改善。tests/test_stage2_vlm.py
  9 例 + 全链绿。
- **状态**：五本对照回归 + 高数新 PDF 全量重跑进行中（用户已修正源 PDF
  页序，新版在 _regress/vlm-lab/pdfs/gaoshu.pdf）。
- **挂账**：db 目录先验自身方差（民法 四、/五、 被锚 L5 vs 兄弟 L4，
  cosmetic，二期交叉校验升级为行动）；`_rule_table_merge` 缺真书 exercising
  （与 042 表格深度挂账合并验证）；stage1 目录提取对同族条目 level 抖动。

### 病例 043 续｜七本回归自迭代：索引污染切除 / 编号句点归一 / 书眉判定不复发化

- **索引页污染目录先验（两机制）**：模型把书末索引页当目录页读，先验整段
  被索引污染（must_defend 368→16 真条目、Feeling Great 569→46），锚定/
  映射后制造数百幻影章（FG 378 空章、md 129 未锚）。修补
  `stage2_vlm._cut_index_tail`：显式 'Index' 条目硬边界（边界后只留真目录
  形状——数字/罗马/章节前缀、日期、前后页家族词，且不带页码串尾巴；
  must_defend 的 four–eight 讲次落界后也保下），无边界回退字母序/页码串
  连续尾 ≥8。配套 `_demote_index_region_titles`：末 30% 书页连续 ≥2 页
  每页 ≥15 个映射标题的区段判为索引区降回正文（FG p527-529 每页 95+
  词条实测）。教训：判据从"词条形状/字母序/深层级"一路错到"显式边界+
  形状救生索"——真实索引有子词条破单调、层级不均、页码被剥，三刀皆钝。
- **编号句点归一**：目录 '12. All-or-Nothing Thinking' ↔ 正文 '12  All-…'、
  'III. X' ↔ 'III X'（印刷目录带句点、正文常无）——`_normalize_title`
  剥首段数字/罗马句点（(?=\D) 防误伤 '3.14'），FG 四个章、QFT '12
  Solutions to exercises' 因此锚上。
- **书眉判定不复发化**：`_demote_running_heads` 原规则"剥号+书眉候选"把
  FG ch12 真章题（章号起首且与书眉同文）误降为 header——正是用户点名
  的"VLM 正确标注被旧规则反向救援误伤"。改：title 块仅当文本**全书复发
  ≥3 次**（title_freq）才降（真章题全书一次，书眉被标 title 必然页页
  复发——QFT '2.2 The Lorentz grou' 实测）；text 块原剥号规则不动。
  中间两版（title 全豁免 / 全文匹配候选）都被实测否掉：前者放出 QFT
  书眉幻影，后者会把与目录同文的真章题（minfa '第一章 民法概念论'）吃掉。
- **重放教训**：手工用 `_to_content_list` 重放 content_list 必须同步
  `_drop_minitoc_lines`（漏调 → QFT 章首 mini-TOC 行锚出 5 个空章）与
  重建 popo（下标漂移 → born 假丢 23 块）。生产管线 parse() 步骤缺一不可。
- **终验（六本 + minfa 早前）**：must_defend 🟢 16/16、qft 🟢 108/108、
  born 🟢 4/4、hanyu 🟡 72/72（固有黄）、gaoshu 🟡 80/80（新 PDF 全量
  重跑，固有黄）、minfa 234/234 全中。Feeling Great 41/43 挂账 3 项：
  'III. …'（长度帽 64 拦晋升——该帽正生于本书 CliffsNotes 案，方向正确）、
  '1. Fifty Ways…'（正文缺块 + em-dash 归一候选）、'3. The Role-Play Tec'
  （空章 1，cosmetic）。
- **状态**：完成（除 FG 挂账）。wiki/09 §6 里程碑同步。

### 病例 043 再续｜交付图片陈旧刷新 + sanitize 长尾

- **`_deliver` 图片陈旧 bug**：`with_images` 此前 `tgt/images 不存在才复制` ——
  老产物残留时新跑图片根本进不了交付目录（新高数 tex 引用 p0022_1 但目录里
  是上一轮老图，12 个图片加载错误）。改：永远 rmtree+copytree 刷新。
- sanitize 长尾：`\left/\right` 后跟间距命令 → 空定界符（'\right\,'
  非法定界符）；unicode 映射补 ∶→\colon、△→\triangle。
- 新高数（修正版 PDF）终态：tex 编译 **0 错 0 缺字符 636 页**；全测试链绿。

## 病例 044｜系统层四连 + VLM 管道表抢救 — annotation/img 尺寸/撞名/图块事故/裸管道

- **T1 MathML annotation**（SageRead 清单 #1）：`_latex_to_mathml` 在
  `</math>` 前注入 `<annotation encoding="application/x-tex">清洗后源码
  </annotation>`（与 alttext 同料，XML 实体转义）。阅读器复制公式可取回
  可编辑 LaTeX。QFT 8576 处、高数 13326 处实测注入。
- **T4 img 物理尺寸**（SageRead 清单 #5）：`_img_size_attr`（PIL 读宽高，
  按 目录/文件名 缓存，读不到不写——铁律 0）接入 `_render_block_to_html`
  与 `_render_popo_body`（后者新加 images_dir 参数）。防阅读器滚动塌陷。
- **输出目录撞名避让**（用户裁定）：同书名重转到同一输出目录不得覆盖。
  `pipeline._unique_book_dir`：目录含既有交付产物（epub/md/tex 任一非空）
  → `书名 (1)/(2)…`；只有缓存（vlm_state.db 等）不算撞名（work_dir 与
  交付根同目录，纯缓存重跑照常原地交付）。registry 增 dir_name 字段。
- **P0 图块丢失事故**（2026-09-17 五书重放）：db.save_page 在图片提取
  **之前**落库 → img_path 只进内存 → 任何从 db 的 content_list 重建都把
  image 块降级文字占位（gaoshu/qft/FG/hanyu/must_defend 图块归零，md 图
  引用却在——md 是原跑内存态生成）。两根修：
  - stage1_vlm 图片提取循环后 `db.save_page` **回写**（img_path/bbox/降级
    固化进 db）；`_needs_image_extract` 守卫：块已带 path 且 PNG 在盘 →
    续跑跳过不重烧 bbox API。
  - `scripts/restore_vlm_images.py`：md 图引用（原跑配对结果 caption+路径）
    ↔ db image 块按页按序配对，caption 归一化不等整页跳过（不错配）。
    五书 190/60/29/85/0 全配对、零警告；回写 db + 走 `_drop_minitoc_lines`
    → `_to_content_list` 原路径重建。四书正式重跑（stage2+3+导出）图全归。
- **T3① 管道表残留**（SageRead 清单 #3，FG 实测 10 处清零）：
  `_table_md_to_html` 三宽容——`_unsquash_pipe_table`（单行压扁表以分隔行
  文本为锚还原多行，避开 '| |' 边界幽灵格歧义；截断残片退表后文本）、
  分隔行允许落前 3 行（双行分组表头 Anger Scale 形态，多行进 thead）、
  单列放行（(✓) 清单表/跨页碎片）；`_split_embedded_table`（t=text 内嵌
  真表切分，前后文本各自成块）；t=table 解析失败回退剥首尾管道符（编号
  列表伪表格）。`_MD_SEP_RE` 重复段 + → *（单列分隔行 `|---|`）。
- **T2 段号转义 '5.\.'**（SageRead 清单 #2）：定位 SageRead 侧 htmd 目录
  提取（EPUB 标题 `<h3>5.5 Wick's theorem…</h3>` 干净，metadata.md 脏）；
  我方数据层无责（七书标题反斜杠扫描 0 命中）。SageRead 已兜底+需重跑
  向量化。
- **T3② 单元格截断**（SageRead 清单 #4）：旧 MinerU 引擎版 FG 的可视边界
  裁剪问题；VLM 版全词无截断（'1—Somewhat/2—Moderately/4—Extremely'）。
- **回归**：tests/test_stage3_annotation.py（13 例）+ test_stage1_vlm_images.py
  （5 例）+ test_stage1_vlm_table.py（16 例）；test_stage1_vlm.py 单列断言
  按新语义改；全测试链绿；gaoshu/qft/FG/hanyu 重建 QC 黄（固有），FG 红 2
  条为已知挂账（III. 长度帽 + Fifty Ways em-dash，T13/T12）。
- **状态**：已修复并验证（FG 挂账照旧）。

## 病例 045｜TeX 导出品控三轮 — booktabs trim / 字体链 / 单元格 display / 列宽

- **`\midrule` 吞单元格**（FG 207 错误）：booktabs 规则（`\toprule/\midrule`）
  后视 `(` 为 trim 参数——首列以 `(` 起头的行（'(Sad) blue…'）被吞。
  修：规则后加 `\relax`。**双错教训**：初修写成 `"\toprule\relax"`（Python
  串 `\r`=回车，产 `\toprule\rel elax` 脏文本），测试串同样写错双双通过；
  二次修复补 `assertNotIn("\rel")` 防回车逃逸。
- **`\slashed` undefined**（QFT 81 错误）：三 preamble 补 `slashed` 宏包。
- **日文 preamble 缺失**（izuno xelatex 段错误+全文缺字符）：`zh` 布尔改
  `lang` 三态，新增 `_TEX_PREAMBLE_JA`（xeCJK + Yu Gothic→MS Gothic→
  Noto→MS Mincho 回退链）；metadata.language='ja' 实测命中。
- **缺字符长尾**：`_TEX_MATH_UNICODE` 补 ✓✔✗✘●➡➜⁽⁾❖；`_TEX_TEXT_EXTRA`
  补制表框线（─│┌┐…→—/|/+）与方向控制符剥除（U+200E/F、软连字符）；
  ZH/EN preamble 主字体 Times New Roman 回退链（西里尔：hanyu 俄语引文
  562 缺字符）；EN preamble 加 xeCJK（`[插图：]` 占位符的 CJK 兜底）+
  **fontspec 必须先于 \IfFontExistsTF**（EN 四书各 7 错实测）。
- **单元格 display 数学级联炸**（jixie 21×Missing $）：longtable l/p 列内
  `\[…\]` 必炸——`_emit_tokens_tex(in_cell=True)` 降级行内 `$…$`。
- **longtable 列宽**（jixie 4386pt Overfull）：'l' 列不换行——`_col_spec_tex`
  按各列最大单元格长度加权 p{…\linewidth} + raggedright；`array` 宏包
  显式进 preamble（该 MiKTeX 内核未自动带 `\arraybackslash`，10531 错实测）。
- **C0 控制字符**（must_defend U+0019）：`_tex_escape` 统一剥除。
- **尾注/目录标题本地化**：`注释/目录` → zh=注释/目录、ja=注/目次、其他=
  Notes/Contents（FG 英文书 '注释' 缺字形实测）。
- **终验**：八核心书 xelatex 全 0 错（gaoshu 810/qft 456/FG 553/hanyu 437/
  minfa 596/must_defend 277/izuno 227/born 213 页），缺字符个位~39；
  jixie（901→页）见下。tests/test_stage3_export.py 新增 7 例。
- **jixie 压测**（T7，498 页表格极限书）：VLM 498/498 页零失败，599 表
  良构（14 列 GLL 规格表多层表头/φ 符号/下标全中），390 图归位（表内嵌
  图 → 独立图块邻接表格，合乎设计），续表 145 处。QC 红 3：Yₓ 下标
  unicode ↔ `$_{\mathrm{X}}$` 数学形态不归一（2 条，normalize 挂账）、
  9.3 节标题 VLM 漏读（正文在，T11 交叉校验正主）；缺页 p16/p497 =
  极淡分隔页/Anna's Archive 水印页（不优化/正确排除）。
- **拆壳顺序事故**：`_TEXT_MATH_SHELL_RE` 首版在 unicode 映射前跑——℃ 被
  映射成 `^\circ\mathrm{C}` 落进 \text{} 壳内（机械手册 42 错尾巴）；改
  为映射后再扫 + `_TEXT_NEST_RE` 塌缩 \text{\text{X}} 双壳。
- **状态**：全部修复并验证——**九书 xelatex 全 0 错 0~39 缺字符**：
  gaoshu 812 / qft 456 / FG 611 / hanyu 439 / minfa 596 / must_defend 277 /
  izuno 227 / born 213 / **jixie 1411 页（0 错 0 缺字符，1411 页表格极限书）**。
  产物已集于 output/v2-review（md/tex/pdf 三格式九本）。

## 病例 046｜MinerU 云端解析失败对半降级 + Yₓ 下标归一 — 1.3.9 用户报告根因

- **现象（用户报告，1.3.9 旧发行版）**：转换中途偶发"解析失败：片 N"；
  Born a Crime 片 1 即败；机械手册（78MB/498 页，未及限额）同样翻车。
- **根因链（两条独立）**：
  1. Born 片 1 即败 = **旧发行版存的是过期 JWT**（与现行代码 401 同款；
     现行代码 + 新 token 复测 Born 全通、QC 全绿）——非管线缺陷。
  2. 机械手册 = **MinerU 云端解析侧失败**（`state=failed` + 'parsing failed,
     please try again later'）：与大小/页数限额无关，是密度压垮 worker——
     实测 200 页/34MB 片败、20 页密扫片败、10 页即过（文字版 Born 200 页
     密排文本反而全过）。旧版无降级，一片失败即整书终止。
- **修补**（stage1_mineru.py）：`_extract_chunk` 云端解析失败时对半递归
  （200→100→50→25→…），单页仍败记入 `failed_pages` 跳过并在日志/返回值
  明示（缺口优于陪葬全书）；网络异常维持重试 3 次后抛（带页码区间）。
- **Yₓ 下标归一**（jixie QC 红 3→1）：`_normalize_title` 新增
  `\math[a-z]+{…}` 包装剥除（先于通用命令剥除，否则剩 'mathrmX' 垃圾字母）
  + unicode 上下标字符 → ASCII 映射表（'Yₓ'/'Yx'/'Y$_{\mathrm{X}}$' 三形态
  归一）；jixie VLM stage2 重跑后 15/15.1 两条锚上。余 '9.3 矩形螺纹的螺旋
  密封计算' = VLM 漏读该节标题（正文在）——T11 交叉校验的正主证据，挂账。
- **T5 核销**：现构建 nav fragment 链接 243 个 0 断链（qft/gaoshu/FG），
  SageRead 清单 #6 的旧构建问题已不存在。
- **回归**：tests/test_stage1_mineru_split_fail.py（对半递归全恢复 + 单页
  隔离，fake client 模拟云端失败）；tests/test_stage2_toc.py 下标归一 3 断言；
  全测试链绿。
- **jixie MinerU 实证（修复后全通）**：498 页密扫（无文本层）4 次对半递归
  （200→100→50→25 页粒度收敛），**零页跳过**，3627 blocks、145.8 万字符、
  **1591 张图**（同书 VLM 版 390 张——规则引擎图像提取强度的直接证据）；
  QC 无红（黄=前页装饰性空章 + 无印刷目录走形状栈）。
- **表内嵌图对照（用户提问）**：MinerU 表格 HTML 原生保留 rowspan/colspan
  合并单元格与单元格内 <img>（EPUB 533 处表内嵌图）——EPUB/XHTML 完全合法；
  md（GFM 单元格仅行内）与 tex（longtable 无 multirow）侧则拍平为纯文本、
  图片外置为独立插图——格式天花板的诚实降级。VLM 侧按设计拆为邻接图块。
- **状态**：已修复并验证（9.3 挂账转 T11）。

### 病例 046 追记｜FG 两条挂账红的归一修复 + 'III.' 深层根因链（挂账续）

- **'1. Fifty Ways…' 已修复**（红→绿）：正文标题尾随脚注星号 `*` +
  目录侧破折号 vs 正文侧冒号双重失配。`_normalize_title` 新增：尾随
  星号剥除 + 分隔符（冒号/各类破折号）归一为 '-'；`_match_anchor`
  长尾规则的分隔限定同步从 ':' 改为 '-'（方向不变，防过匹配）。
- **长度帽豁免收窄**（`_calibrate_levels` 救援）：>64 字符非标题块的
  全键精确命中豁免长度帽（71 字符随机碰撞不可能），模糊/前缀命中仍受帽
  （CliffsNotes 案由位置闸门兜底）。tests/test_stage2_toc.py 新增
  健康家族前提下的晋升/拦截双断言（64/64 绿）。
- **'III. The Spiritual/…' 仍红——根因链完全查明，修复需裁定**：
  ① 该书四个编分隔页全被 VLM 标成 text；② I./II. 的标题块落在 p10
  印刷目录页，被目录页降格吃掉；③ 目录先验里 I./II. 条目干脆缺失
  （VLM 读目录页漏收）；④ III. 正文块经锚点精确命中+豁免晋升后，
  孤儿编号族判（罗马家族全书无 I./II.）把它降回正文。要救需动
  孤儿罚语义（豁免锚点救援块）或 VLM 目录页条目补收——均属既有语义
  改动，按 AGENTS.md 留待用户裁定。QC 现 42/43，空章 1（已知 cosmetic）。
- **状态**：Fifty Ways 已修复并验证；III. 挂账（根因链存档）。
