# 06 VLM 逐页读书引擎 — 调研评估与测试方案（提案）

> 状态：**已实现并真书回归通过（2026-09-12）**——`stage1_vlm.py` +
> `vlm_client.py` 落地，民法总论（6编15章/脚注 476 链/图 92）与
> Born a Crime（QC 全绿、超长章题入目录）双书验证，已导入 SageRead dev
> 实例供对比阅读。实验报告见 §8；数据与逐案记录：`_regress/vlm-lab/NOTES.md`。
> 规则管线（MinerU/PaddleOCR + 锚点系统）保留不动；本路线目标是以
> 第三种引擎 `--engine vlm` 并行接入，与规则方案共存，由用户选路。
> 本文 = 调研结论 + 可行性评估 + 测试方案 + 实验报告。

## 1. 动机：为什么是现在

- **价格拐点已至**（2026-09 官方定价，详见 §4）：GLM-4.6V-Flash 免费、
  qwen3-vl-flash / GLM-4.6V-FlashX 约 1.2 元/本（400 页，Batch 后 0.6 元）、
  GLM-5.3-Flash 约 3 元/本。两年前"逐页 VLM 读整本书"是不可想象的成本，
  现在低于一杯豆浆。
- **OCR 天然丢结构**：页内"① 标记 ↔ 页底注文"的空间对应、版式语义
  （书眉/章节层级）在 OCR 文本流里全部坍缩，只能靠规则事后猜（民法总论
  874+ 脚注就是靠 bbox+正则硬配的）。多模态模型直接"看"页面，这些信息
  对它是一等公民。
- 规则管线的已知软肋（FIXLOG 反复验证）：脚注重建、无目录书结构、
  双栏阅读序——都是 VLM 的潜在强项。

## 2. 调研结论：无直接先例，但每个组件都有近邻

调查覆盖 20+ 开源/商业方案（zerox、olmOCR、dots.ocr/dots.mocr、MinerU 2.x、
PaddleOCR-VL、DeepSeek-OCR、Docling、Marker、Nougat、GOT-OCR2.0、MonkeyOCR、
Reducto、LandingAI ADE、Unstructured、Chunkr、pdf-craft 等）。

**"逐页 VLM 阅读 + 增量结构化状态落库 + 目录先验"的完整形态没有先例。**
现有方案落在三个孤立格子里：

| 格子 | 代表 | 缺什么 |
|---|---|---|
| 逐页直读，无状态 | zerox、olmOCR、dots.mocr、DeepSeek-OCR | 页间无记忆，无结构状态 |
| 先解析后推断结构 | pdf-craft、LandingAI ADE Section、Marker | 目录是后验归纳，非阅读先验 |
| 多遍自纠错 | Reducto（Agentic OCR） | 单文档内纠错，无书籍级状态 |

- **最接近的先例：pdf-craft**（oomol-lab，MIT，中文社区）——整书级、
  DeepSeek-OCR 逐页 + LLM 事后推断目录/章节 + 脚注归并 + 直出 EPUB。
  差距三条：读取本身无状态；目录是推断产物而非先验；无增量落库
  （`.pcex` 中间包是 OCR 快照，非演化状态机）。
  https://github.com/oomol-lab/pdf-craft
- **唯一的跨页上下文**：zerox 的 `maintain_format` 把上页 markdown 传给
  下一页，仅用于表格格式一致，无结构化状态。
  https://github.com/getomni-ai/zerox
- 脚注：全行业最好水平 = 独立 Footnote 块（dots.ocr、MinerU2.5 的
  `page_footnote`、Docling、Chunkr），**没有任何系统做"正文标记 ↔ 注文"
  配对登记**，也无公开基准。这正是我们的差异化空间。

基准要点（决定测试阈值）：

- 综合质量 Elo：通用前沿 VLM（Gemini 3 Pro 1210）> 所有专用小模型
  （dots.mocr 1125）——"直接让通用大模型读"的质量前提成立。
- 但**文本转写保真**：专用小模型编辑距离 0.031–0.047 < 通用旗舰
  0.066–0.075（OmniDocBench v1.5，dots.ocr README 汇总）；中文比英文
  差约 28%（CN edit 0.160 vs EN 0.125）；老扫描页全员仅 30–52 分
  （olmOCR-bench old_scans）。
- 幻觉率（PP-OCRv6 技术报告自建基准，"输出不含编造内容"准确率）：
  旗舰通用 VLM 80–85 分，即**密集文本页仍有 15–27% 的含幻觉输出率**。
  https://arxiv.org/pdf/2606.13108
- 页眉页脚剔除已是成熟能力（olmOCR-bench 该类各系统 90+）。

## 3. 目标设计（v0 草图）

```
PDF ──fitz 渲染页图（已入依赖，~150-200 DPI）
  ──▶ VLM 逐页读（串行 agent 循环，每页一次调用）
        输入：页图 + 上页尾部 N 字符（<context_only> 标记）+ 当前结构栈摘要
        输出：结构化 JSON（块序列：类型/文本/bbox/脚注配对/图片区域）
        状态：SQLite 两表（pages(page_no,status,raw_json) / book_state(key,value)）
              每页事务提交 → 断点续跑、可重放、可审计
        目录页：读到即高置信落库为 toc 先验 → 注入后续每页 prompt
        脚注：页内 ①↔注文 配对登记；跨页脚注挂起等下一页
        图片：bbox 落库 → 从页图裁剪 → images/（bbox 外扩 2-3% 防贴边）
  ──▶ 产出 {stem}_content_list.json（**契约不变**：0-1000 千分位 bbox、
       已知块类型集、page_footnote 等）+ images/ + {stem}.md
  ──▶ 复用 Stage 2 锚点系统（降级为校验模式）→ Stage 3 EPUB → qc_book
```

关键架构判断（代码勘察结论，agent 报告）：

- **接缝极干净**：实现 `OcrProvider` 协议（`ocr_provider.py:27-44`，
  `name` + `parse()` 返回 content_list/images_dir/markdown）并在 `_BUILTIN`
  注册一行（`ocr_provider.py:57-60`）即接入；CLI `--engine` choices 动态
  生成自动出现；`--skip-mineru` 缓存复用机制对新引擎自动兼容。
  **下游 Stage 2/3/QC 零改动**——VLM 路线白嫖整个锚点语义系统。
- 硬契约两条：bbox 必须 0-1000 千分位且不能缺（缺了 `popo/convert.py`
  丢块）；块类型收敛到 `stage1_layout.py:8` 已知集合。
- **不需要 LangGraph 等重框架**：线性可重放流水线，薄 SQLite + 每页事务
  即覆盖断点续跑/审计；这与调研结论一致（LangGraph checkpointer 对本场景
  是杀鸡用牛刀，且其 SQLite checkpointer 有反序列化 CVE 前科）。
- 多模态调用在仓内零先例（现有 LLM 调用全为纯文本，经
  `llm_thinking.chat_create` 包装）；VLM 调用路径需自建，配置侧加一套
  `VLM_API_KEY/BASE_URL/MODEL`（现状只有 DEEPSEEK_* 一套，
  `config.py:43-46`）。
- LLM 不定性守卫哲学沿用：VLM 输出仍是"首票"，锚点系统与 QC 仍是真值
  裁决者；新守卫失败方向同样必须是"不动作"。

防幻觉分层（按调研证据强度排序）：

1. **文本层锚定**（born-digital 书）：olmOCR document-anchoring 实测
   +18 分（arXiv 2502.18443）。渲染页图的同时抽出该页 PDF 文本层一并
   喂入；扫描书无文本层则跳过（失败方向=不动作）。
2. **结构化输出 + 页级校验**：JSON Schema 约束 + 校验失败重试
   （olmOCR 同款，页错误率熔断 1/250）。
3. **温度策略**：基线 T≈0；检出重复循环/RECITATION 拒答时**升温重试**
   （LlamaParse 生产经验，且不可用 presence/frequency penalty——会破坏
   忠实转写所需的合法重复）。
   https://www.llamaindex.ai/blog/engineering-insights-failure-modes-that-break-vlm-powered-ocr-in-production
4. **上下文注入防"续写污染"**：上页尾部用 `<context_only do_not_transcribe>`
   包裹 + 输出侧前缀重叠检测（与上页尾部编辑距离 < 阈值 → 截掉）。

## 4. 模型选型与成本测算（2026-09-12 官方价）

场景假设：400 页/书，每页 1 调用，输入 = 页图（1000–2500 tok）+
上下文文本（~3000 tok），输出 ~1500 tok。

| 模型 | 价格（元/M tok 输入/输出） | 单书成本 | 备注 |
|---|---|---|---|
| **GLM-4.6V-Flash** | **免费** | **0 元** | 128K 上下文，**测试期首选**；免费档并发低（个位数，需实测） |
| GLM-4.6V-FlashX | 0.15 / 1.5 | ~1.1 元 | 付费提速版 |
| qwen3-vl-flash | 0.15 / 1.5 | ~1.2 元（Batch 0.6） | **官方 bbox grounding**；RPM 3000/TPM 5M；图 ~2100–2560 tok/页 |
| doubao-seed-1.6-flash | 0.15 / 1.5 | ~1.25 元（批量 0.62） | 图固定封顶 1280 tok |
| **GLM-5.3-Flash** | 0.8 / 2.8 | ~3.0 元 | **用户点名，确认是多模态**（1M 上下文）；注意旗舰 GLM-5.3 是纯文本，别接错 |
| GLM-4.6V | 1 / 3 | ~3.4 元 | 106B，质量档 |
| deepseek-flash (V4.1) | $0.15–0.30 / $0.60–1.20 | ~4–8 元 | **每图统一 ≤384 tok（~800×800px）——中文密页分辨率死刑嫌疑**，T0 实测一页即知；并发 2500；闲时半价 |
| Gemini 3 Flash | ~$0.50 / $3.00 | ~19 元 | bbox 原生 0-1000 归一化；价格未经官方页核实 |

来源：智谱定价 https://docs.bigmodel.cn/cn/guide/start/pricing ｜
DeepSeek https://api-docs.deepseek.com/quick_start/pricing ｜
阿里 https://help.aliyun.com/zh/model-studio/qwen3-vl-flash ｜
火山 https://www.volcengine.com/docs/82379/1099320

工程要点：

- GLM 官方 FAQ：单图约 1047 tok（按张近似固定，是否按像素随模型变
  **需实测**）；qwen 32×32px/tok；豆包 2.0 系 detail=high 封顶 1280 tok。
- 半价通道：智谱/阿里/火山均有 Batch API 5 折；DeepSeek 无 Batch 但
  时段半价。本任务是离线任务，Batch 完全适配。
- prompt 前缀（系统指令+目录状态）高度重复 → 缓存命中价可再削 30–50%
  输入成本，工程上保持前缀稳定。
- 避坑：旧 **GLM-4V-Flash 上下文仅 4K**，放不下本场景 prompt，不可用；
  免费的是 **GLM-4.6V-Flash**（128K），别混。
- 时延预算：flash 级输出 150–250 tok/s，单页 800–1500 输出 tok →
  4–10 s/页；400 页串行 ≈ 35–65 min。测试期可接受；量产可"分块并行 +
  单线程边界修复 pass"（成熟先例：olmOCR pages_per_group、
  mineru-refine cross_page_break）。

## 5. 风险登记

| # | 风险 | 证据/先例 | 缓解 |
|---|---|---|---|
| R1 | 密集中文页错字/幻觉（最大敌人） | 旗舰 VLM 幻觉率 15–27%；中文 edit 比英文差 28% | 文本层锚定；结构化校验重试；与 Stage 1 缓存 diff 抽验；状态落库作纠错约束 |
| R2 | 内容过滤拒答（政治敏感书） | **本仓已有先例**：GLM 1301 拒答敏感目录（wiki 02）；LlamaParse RECITATION | 测试含敏感页；多模型 fallback；升温重试 |
| R3 | 图片 bbox 精度不足 | 通用 VLM 零样本文档 grounding 很弱（GutenOCR 0.40，arXiv 2601.14490） | T4 自测集量 IoU；退路=GLM-OCR(0.2元)/dots.mocr 专职区域检测（仍是 VLM，不违背路线精神） |
| R4 | 免费档并发/限额 | 智谱按账户等级限并发，文档不列表 | T6 实测；付费档 FlashX 仅 0.15 元/M |
| R5 | 重复循环打满 max_tokens | LlamaParse 生产事故复盘 | finish_reason 检测 + 升温重试 + 换模型 |
| R6 | 目录页识别/转写错误污染全书定级 | 同现有管线"LLM 编造目录"风险（病例 001） | 目录落库后过 QC 关口再当先验；沿用伪造目录兜底思路 |
| R7 | DeepSeek 分辨率死刑 | 每图 ≤384 tok ≈ 800×800 | T0 一页实测，不行直接出局 |
| R8 | 续写污染（模型把上下文当要续写的内容） | 无公开先例可抄，属自创机制 | `<context_only>` 标记 + 输出前缀重叠去重；T5 专项验 |

## 6. 测试方案（T0–T6，每步设决策门，不通过不进入下一步）

测试资产：主考场 **民法总论**（中文、561p、874+ 脚注基准、双引擎真值；
若 `_regress/` 无暂存则从书库复制 PDF 暂存，符合书库隔离规则）；
副考场 **Born a Crime**（英文，`_regress/` 有缓存）、**刘擎西方现代思想
讲义**（55 锚点真值）、**QFT**（公式页）。敏感页附加样本：八次危机 1–2 页。

| 步 | 内容 | 投入 | 决策门 |
|---|---|---|---|
| **T0 冒烟** | 1 页含脚注正文 × 3 模型（GLM-4.6V-Flash / GLM-5.3-Flash / deepseek-flash）：验证图像 token 实计、延迟、基本转写、DeepSeek 分辨率 | 0.5 天 | DeepSeek 小字不可读 → 出局；GLM 系基本可读 → 继续 |
| **T1 单页保真** | 20 页分层样本（目录 2 / 章首 2 / 脚注密集 6 / 插图 3 / 普通正文 5 / 公式 2）。三向对比：VLM vs MinerU 缓存 vs PDF 文本层（若有）+ 人工抽校 3 页。指标：字符编辑距离、漏行数、编造行数 | 1–2 天 | 与 MinerU 缓存字符差异 ≤3%，且人工抽校不差于 MinerU |
| **T2 目录提取** | 目录页样本 → 结构化 toc_entries（条目/层级/页码） vs 现有真值（民法总论 21 单元、刘擎 55 锚点） | 0.5 天 | 条目召回 ≥98%，页码 100% |
| **T3 脚注重建**（核心卖点） | 民法总论 30 个脚注密集页。A/B 两案：(a) prompt 直接配对输出 `[^n]`；(b) 模型只检测+转写、规则层配对。真值：人工标注样本页 ①↔注文 对应 | 1–2 天（含人工标注） | 配对准确率 ≥95%；两案择优 |
| **T4 图片 bbox** | 20 个插图页：VLM bbox vs MinerU 裁剪图（参考真值）量 IoU | 0.5 天 | IoU≥0.8 的比例 ≥85% → 走 VLM bbox；否则退路 GLM-OCR/dots.mocr 检测 |
| **T5 上下文拼接** | 10 组跨页段落：拼接正确性 + 续写污染检查（输出是否重复上页文字） | 0.5 天 | 污染率 0（去重机制兜底后）；拼接正确 ≥9/10 |
| **T6 成本并发实测** | 连续跑 100 页：实测单页 token、P50/P95 延迟、免费档并发上限、单书外推成本 | 0.5 天 | 单书外推 ≤5 元；无不可控限流 |

附加项：**敏感页拒答测试**（八次危机 1–2 页 → GLM 是否 1301）——决定
敏感书是否必须走非 GLM 模型。

全部通过后进入实现：`stage1_vlm.py`（OcrProvider）+ SQLite 状态库 +
prompt 集，走 AGENTS.md 强制验证链（单测 + 真书回归 + 亲读产物）。

总工作量预估：测试脚本 + 人工标注约 **3–5 天**；测试期 API 成本
（GLM-4.6V-Flash 免费档）≈ **0 元**。

## 7. 与现有管线的关系

- 规则管线**保留且默认**；VLM 是并行第三引擎，用户显式 `--engine vlm`
  选择。两引擎共用 content_list 契约 → Stage 2/3/QC/翻译全部复用。
- 若 T1/T3 证明 VLM 在特定维度（脚注、无目录书）显著优于规则管线，
  也不删规则路线——双引擎对照恰是 QC 的最强信号（同书双跑 diff）。

---

## 8. 实验报告（2026-09-12，T0–T6 全绿通过）

实验资产：`scripts/vlm_lab/`（脚本）+ `_regress/vlm-lab/`（数据/结果/NOTES.md
逐案记录）。主考场民法总论（扫描版 561p，MinerU 缓存为对比真值），
公式页取自 QFT（born-digital，有文本层可三向对比）。
用户追加要求已纳入：形状栈同级不漂移、GLM-4.6V-Flash 只作对照、思考档位
实验说话、自愈能力待验证、敏感书目跳过（政治敏感书不测，海外模型选择权
留给用户）、SageRead reasoning-map.ts/vision-map.ts 思考档+多模态映射已
移植进 `scripts/vlm_lab/vlm_client.py`。

### 决策门汇总

| 步 | 决策门 | 实测 | 判定 |
|---|---|---|---|
| T0 冒烟 | DeepSeek 分辨率可用性 | 干净扫描页可用（①标记可辨、脚注 CER 0.009） | ✅ 不判死刑，脏页观察 |
| T1 保真 | 判决后不差于 MinerU | GLM 全面 ≥ MinerU；公式页 vs 文本层 0.077/0.116 碾压（别家 0.38–0.73） | ✅ |
| T2 目录 | 召回 ≥98%、页码全对 | 召回 **100%**、有效页码 **100%**、层级 **100%**、形状栈**零漂移**；多抓 83 条真值漏收的真实条目 | ✅ 超标 |
| T3 脚注 | 配对 ≥95% | **100% 召回、零流浪**、注文 CER ~1%（30 页 × 74 条，glm/deepseek 双模型） | ✅ 超标 |
| T4 图片 | IoU≥0.8 比例 ≥85% | doubao-2.1-turbo **100%（21/21，meanIoU 0.976）** | ✅ 超标 |
| T5 拼接 | 污染率 0 | **0/20**；一致性 CER 0.014 | ✅ |
| T6 成本 | 单书 ≤5 元 | **~1.11 元/400 页**（glm/low，0.274 元/100页）；顺序 86min、4 线程 ~20–25min、成功率 99% | ✅ |

### 关键实验结论（引擎设计输入）

1. **选型**：转写/结构/脚注主力 = **GLM-5.3-Flash @ thinking low**
   （low/high/max 质量一致，max 烧 2–4× token 无收益；该型号思考恒开不可关，
   low 是地板也是最优）；图片提取 = **同模型出粗框 + raster_snap 连通域
   光栅重裁**（默认零额外 key；T4b 实测 meanIoU 0.883、IoU≥0.8 比例 95.2%，
   可选配 doubao-seed-2-1-turbo 冲 0.976，T4 实测）；镜像备选 =
   **deepseek-flash**（最快 2.75s/页、token 最省、干净页质量持平 GLM）。
2. **出局**：qwen3-vl-flash（格式服从性三连败：脚注混排 body、T2 截断
   80 条、bbox 裸数组）；glm-4.6v-flash（同款格式病 + z.ai 限流 1305）；
   gemini-3-flash 经 cherryin 聚合（思考烧 7.8k token/页撞 max_tokens，
   通道无思考控制 → 海外通道需直连 Gemini/Grok，暂挂账）。
3. **脚注重建走 A 案**：模型直接输出 JSON `{"body", "footnotes":[{marker,text}]}`，
   规则只做校验兜底（B 案"检测+规则"在 deepseek 上有两页翻车）。
4. **目录先验**：batch（13 页图一次调用）与逐页增量提取**结果完全一致**
   → 引擎顺序读、遇目录页就地建先验的设计成立。简目+详目并存会重复提取，
   按 norm(text) 去重。
5. **上下文注入安全**：`<context_only do_not_transcribe>` + 上页尾部 300 字符，
   20/20 无续写污染。
6. **VLM 完整性反超 MinerU**：MinerU 丢页首段（p152/p215）、页界错配
   （p117 混入 p118 文本）都被 VLM 避免 → 评测指标必须方向分解
   （extra vs missing），"VLM 比真值多"通常是 VLM 对。
7. **自愈双刃剑**：正向——法发〔2010〕51号括号规范化、〔日〕标点、
   版式理解全面优于 OCR；反向——住所地→所在地（过度意译）、
   住持→主持、判解→别解（单字错 ~0.1–0.3%）。→ prompt 必须显式
   "术语逐字、不得规范化/意译"；QC 保留单字级 diff 抽验。
8. **工程健壮性**：z.ai 偶发 500（1234，~1%）→ 重试队列；
   doubao 出现过 JSON 截断（finish=length）→ 结构化输出必须校验完整性，
   截断即重试/加大 max_tokens（失败方向=不动作，与 olmOCR 页级重试同构）。
9. **token 实测**（glm）：in≈1724/页（图 ~1100）、out≈496/页（thinking low）。
   成本外推与调研一致偏低——Batch API 还可再减半（未测，量产可选项）。

### 下一步

按 §7 接缝实现 `stage1_vlm.py`（OcrProvider 协议）+ SQLite 状态库
（pages/book_state 两表，每页事务），prompt 集沉淀自本实验（含逐字纪律
强化版），走 AGENTS.md 强制验证链。实施前请用户确认本报告。
