# 10 待办总账 v2（2026-09-17 整理，待用户拍板优先级）

> 汇总全部挂账/新发现/外部输入。分级：P0 内容质量必修 / P1 重要能力 /
> P2 打磨与二期 / 挂账-观察（原则不动）。每条含出处与验收口径。
> 前置结论见 §0；讨论后按拍板顺序执行。
>
> **进度（2026-09-18 凌晨）**：T1/T2/T3①②/T4/T6/T7 ✅ 完成（病例 044/045）。
> T2 定位 SageRead 侧 htmd；T3② 旧 MinerU 版问题；T6 代码+单测完成，
> 云端实证挂账（MINERU_TOKEN 401 过期，需用户刷新）；T7 机械手册压测
> 通过（498/498 页零失败、599 表良构、产物已进 dev 书库）。
> 产物：八书 + 机械手册 VLM 三格式 + TeX 编译 PDF（output/v2-review），
> EPUB 已打 VLM/规则标签导入 dev 实例（零进度测试副本已清，向量化排队中）。
> 余：T5、T8 等发令、P2 各项。

## 0. 前置结论（本轮已查实，供讨论）

- **200MB+ 上传失败根因（已实证）**：`stage1_mineru.py` 按页数分片
  （CHUNK_SIZE=200），但 `client.extract(完整文件路径, pages="1-200")` 的
  SDK 行为是 `_upload_and_submit([整份文件])`——**每片都重新上传整份 PDF**，
  pages 只是解析侧筛选。所以云端单文件大小限制（估 ~200MB）下，
  200MB+ 必炸，且大文件被重复上传 N 次（也是慢的原因之一）。
  修法：fitz 物理切片成 ≤100MB（且 ≤200 页）的 chunk 文件再逐片上传，
  页码偏移逻辑沿用现有 page_offset。
- **固有黄判读**：大部分是书的固有形态（印刷目录本就不收小节 → orphan
  数字小节 27–111 个/书），不修；少数是 QC 判据可更精细（低优先）；
  个别 cosmetic 空章（高数 反三角函数/附录Ⅳ）内容未丢，低优先。
- **VLM 表格现状（用户最关心）**：Feeling Great 164 张表良构（quiz/量表
  thead/tbody 齐）是最强证据；高数表格含公式单元格已根治（041）。
  **未验证/薄弱**：跨页合并（`_rule_table_merge` 缺真书 exercising）、
  多层表头、表格嵌图片、单元格文字截断（SageRead 清单 #4 实证）、
  纯 Markdown 表格残留进正文（SageRead 清单 #3 实证）。
  → 用机械设计手册做极限压测补齐（§1.T3）。

## 1. P0 内容质量（SageRead 交接清单，原文 F:/MyProjects/SageRead/docs/plans/books-converter-optimizations.md）

- **T1. MathML 保留原始 LaTeX（annotation）**：stage3_epub `_latex_to_mathml`
  生成 `<math>` 时写入 `<annotation encoding="application/x-tex">`。
  MathML 原生元素、零兼容风险；SageRead 向量化/Agent 直接受益（最高优先，
  他们点名 #1+#5 先行）。验收：QFT 重跑后抽 `<math>` 含 annotation。
- **T2. 标题段号转义吃数字（'5.5'→'5.\.'）**：QFT 小节号在向量化 metadata/
  正文被转义吞数字。先在我们的 EPUB/XHTML 里复现定位（grep `5\.\\\.`），
  修转义只动 `.` 不吞数字；SageRead 侧已有兜底，我们修数据层根因。
- **T3. 表格三线压测 + 两项实证修复**：
  ① 纯 Markdown 管道表残留进 `<p>`（Feeling Great ch2 实证）——查
  `_md_table_to_html` 失败路径，失败时至少转义管道符/包 `<pre>`；
  ② 单元格文字按可视边界截断（'Somev'/'Modern'/'Extrem' 实证）——
  先定引擎归属（该版 FG 是 MinerU 还是 VLM 转的），MinerU 侧则评估
  是否 vendor 问题，VLM 侧则 prompt 禁止截断；
  ③ 机械设计手册极限压测（表格嵌图片、多层表头、跨页表）。
- **T4. `<img>` 写物理尺寸**：stage3_epub 插图时读 PNG 实际宽高写
  width/height 属性（SageRead 滚动位置追踪根治的源头）。
- **T5. TOC href 与 spine 拆分对齐**：拆 spine 时 fragment id 必须在
  拆分后文件里真实存在，或每拆分文件配无 fragment 的 TOC 项。
- **T6. 200MB+ 双天花板切片上传**：云端硬限制=单文件 ≤200MB 且 ≤600 页
  （SDK FileTooLargeError/PageLimitError 实证）。当前假分片（按 200 页分但
  每片重传整份文件）。修法：fitz 物理切片——顺序累积页，页数将达 200 或
  片字节将达 **150MB**（200MB 硬顶的 75 折余量）即切一刀；单页超阈值的
  极端高清单页 → 该页降采样重嵌（明示日志，不陪葬全书）。切片后 pages
  参数废弃，逐 chunk 全量解析按序拼接（page_offset 沿用）。单片重试 3 次
  仍失败 → 整书终止但错误信息带失败 chunk 页码区间。回归一本大文件书验证。
  （后话可选：批量接口 ≤50 文件/次，切片天然适配，上传 N→1 次提速。）

## 2. P1 重要能力

- **T7. 机械设计手册全本压测**（C:\Users\20995\Downloads\机械设计手册…
  润滑与密封….pdf）：先复制到 `_regress/` 暂存。VLM 全管线 + QC +
  表格专项审查（嵌图片单元格去向、多层表头、跨页合并触发率），
  产物进 dev 书库亲读。顺带覆盖 §T3③。
- **T8. v2.0.0 打包发布（M4）**：version.py → 2.0.0、sidecar 重打
  （books_converter_cli.exe 随 GUI）、MSI/zip、自动更新链。**用户发令才动**。
- **T9. MinerU 4.0 跟踪**：`MINERU_MODEL` 预留 tier 取值（flash/basic/
  standard/advanced）而非写死 vlm；盯云侧 tier 上线。

## 3. P2 打磨与二期

- **T10. MinerU 4.0 本地可行性实测**（用户指令：必测 VRAM 占用与整书
  wall time；占满显存跑数小时=无用，几十分钟=极有价值 → 评估 GUI 设置页
  与 SageRead 侧"一键部署调用"）。测材：一本中等书 + 本机 GPU。
- **T11. 二期交叉校验升级为行动**（目录先验↔正文标题差异超阈值重读该页；
  伊豆'供養/牧場'类单点误读的正主）。
- **T12. em-dash 归一进 `_normalize_title`**（—/–/- 统一；FG '1. Fifty
  Ways…' 锚不上候选）。
- **T13. FG 挂账 3 项**（'III.' 长标题被 64 长度帽拦——帽生于本书案例
  不松、'1. Fifty Ways…'、'3. The Role-Play Tec' 空章 cosmetic）。
- **T14. db 先验方差**（民法 四、/五、 锚 L5 vs 兄弟 L4，cosmetic）。
- **T15. 伊豆の踊子挂账**（锚点⊆块方向新规则——需用户批准；'乙女の港'
  空章层级语义）。
- **T16. 老日文/老扫描件**（19 世纪报纸类）：不修（用户裁定）。

## 4. 挂账-观察

- SageRead 阅读器侧渲染项（他们自己在修，避免撞车）。
- 政治敏感书海外模型自由：已完成（GUI 预设 OpenRouter/Gemini/Grok/自定义
  OpenAI 兼容端点；reasoning-map 已移植 vlm_client）。
- 幻觉自愈边界：prompt 已显式"术语逐字"；QC 保留单字级 diff 抽验。
- 伊豆老日文两引擎共同天花板：不强行优化（用户裁定）。

## 5. 拍板用：建议执行顺序

1. T1（annotation，小改大收益）→ T4（img 尺寸，小改）→ T2（转义吃数字，
  根因定位可能较深）→ T3①②（表格两实证）→ T6（200MB 切片）
2. T7 压测（可与 1 并行，花的是 API 额度与等待时间）
3. T8 打包（等发令）；T10 本地实测（等压测空窗）
4. P2 各项随自迭代带掉

待确认问题：
- T3② 的 FG 英文版是用哪个引擎转的（决定截断修复落在哪侧）？
- T7 压测产物是否照例进 SageRead dev 书库亲读？
- T8 v2.0.0 发令时间是否仍等"验证完"？
