# Books_Converter 改造计划 v4（已落地）

> v4：放弃"全书塞给 DeepSeek"的 v3 构想，改用 MinerU-Popo 本地 4B 模型做文档结构重建。
> 基于：Popo 论文(arXiv:2605.24973) + 官方仓库源码分析 + 真实推理验证。

---

## 〇、为什么是 Popo 而不是 v3 的"仿生语义分析"

v3 计划的三层判断（TOC 匹配 + 编号模式 + LLM 兜底）本质上是在用规则+通用 LLM
重新发明 Popo 已经用专用模型解决掉的轮子。Popo 的四个子任务正好覆盖全部痛点：

| 痛点 | Popo 子任务 | 替代了 v3 的什么 |
|------|------------|-----------------|
| 标题层级管理 | 标题层级重建（TEDS 53.7→90.6） | TOC 匹配 + 编号正则 + LLM 判断 |
| 跨页文本拼接 | 文本截断恢复 | Stage 3 的 `_merge_broken_paragraphs` 正则硬拼 |
| 跨页表格合并 | 表格截断恢复 | 无（v3 完全没处理） |
| 注释/图注归属 | 图文关联 | 无 |

关键事实：**MinerU 官方云 API 不提供 Popo**（云 API 只有 pipeline/vlm/MinerU-HTML
三种 model_version），Popo 只以开源形式发布（GitHub MIT + HF 权重
`DreamEternal/MinerU-Popo`，Qwen3-VL-4B 微调）。但 Popo 的输入是 OCR 后的
**文本 block 序列**而非页面图像，推理量远小于 DeepSeek OCR 那种逐页重识别方案。
实测 RTX 5070 Ti Laptop(12GB)：bf16 加载占 8.3GB 显存，模型加载 12s。

（MinerU 3.3/3.4 的 Hybrid 引擎与 effort 参数、PP-OCRv6 均只影响本地部署版，
云 API 未暴露，对本管线无直接影响。）

## 一、v4 架构

```
Stage 1: MinerU 云 API (vlm) —— 不变，免费
Stage 2: popo/ (vendor 自官方仓库, MIT)
           convert.py    content_list → Popo 统一输入（自写，替代官方 label_normalization）
           inference.py  四项任务推理（串行，transformers 本地后端）
           tree.py       标注 blocks → 文档树
         stage2_popo.py  编排 + DeepSeek 轻量兜底（一次调用：metadata + 前后页分类）
Stage 3: engine=="popo" 时走线性渲染：按阅读顺序遍历标注 blocks，
         level→h-tag/章节边界，contd→段落合并（含英文断词 dehyphen），
         table_merge→跨页表格合并，image 关联→图注挂载
```

structure.json v2：`{engine:"popo", metadata, front_matter, back_matter, tree,
popo_blocks_file}`；标注 blocks 单独存 `popo_blocks.json`。

DeepSeek 的角色从"全书结构分析"降级为"轻量兜底"：只发书首 15 页 + 书尾 5 页
采样（每页 800 字符），一次调用拿 metadata 和 front/back matter 页码分类，
成本约 $0.002/本。彻底去掉会导致 metadata/前后页靠规则猜，质量下降。

## 二、已验证

- 构造数据端到端（stage3 Popo 路径）：编/章/节切分、跨页段落合并、英文断词、
  图注挂载、表格渲染、3 层 TOC，10/10 通过
- 真实推理《城市与国家财富》(375 页扫描本，2026-07-19)：
  - Popo 全本推理 **601s**（chunk_size=12；chunk=50 时 50 页就要 488s）
  - 920 个标注 block：跨页拼接 31 处、标题 53 个、图文关联 6 处
  - DeepSeek 轻量兜底：metadata 全对（简·雅各布斯/金洁/中信出版集团），
    front matter 精确到页（封面 P1/扉页 P2/版权 P3/献词 P4/目录 P5-7）
  - 最终 EPUB：14 章完整标题（裸标题经 toc_entries 富化：
    "第一章"→"第一章 蠢材的天堂"），批注与评语/致谢正确归入后页
- 实施中修掉的三个坑：
  1. **Popo level 只有相对意义**（跨分块 bias 校正会整体平移，mini 里章=L1，
     全本变 L2）→ spine/partition 判定改为看顶层标题用词（编/篇/卷/部/Part…），
     不依赖绝对 level
  2. **后页边界不能只靠首尾采样猜**：把 Popo 标题列表（页码+层级，仅几十行）
     喂给 DeepSeek 轻量兜底，back_matter 页码以标题实际页码为准
  3. **chunk_size 50 是为大显存 vLLM 设计的**：VL 注意力随拼接页数平方增长，
     本地 12GB 显存改 12，全本从估算 ~3h 降到 ~10min

## 三、遗留问题 / TODO

- 章标题被 OCR 拆开时（"第一章"+"蠢材的天堂"两块），富化后副标题块仍以
  普通段落残留在章首（内容不丢，视觉上像小标题重复一次），后续可做相邻块合并
- 页脚注(page_footnote)目前仍然丢弃（与旧管线一致），Popo 的图文关联不支持
  正文脚注与正文锚点的挂接，需另行设计
- generate_metadata/split_subnode（节点摘要增强）未 vendor，与 EPUB 生成无关
- 公式目前按文本渲染（LaTeX 源码），MathML/SVG 转换仍是 P1
- 待测书目：高等数学（公式）、The German Ideology（英文，断词/dehyphen 实战）

---

## 四、层级漂移问题（民法总论实测）与 TOC 锚定 + 形状栈校正

### 问题实锤（2026-07-19，用 popo_raw 分块记录分析）

上游 Popo 的跨分块 bias 校正在深结构书上**复利漂移**：
- 民法总论 561 页、47 个分块：校正链 0 → -1 → -5 → -7 → -8 一路累计，
  到书的中段普通列表项被判成 L13~L15
- 重叠标题判定一致性仅 46%（58 一致 vs 67 冲突），avg_bias 本质随机游走
- 单分块首票本身也可能失真（如把 "1. 2. 3." 列表项判成 L2/L3 顶层）

结论：**Popo 的绝对 level 不可信**，必须外部校准。

### 校正方案（stage2_popo._calibrate_levels）

两类稳定信号，不用 Popo 绝对 level：
1. **TOC 锚点**（绝对真值）：DeepSeek 轻量兜底提取带 level 的目录条目，
   正文标题归一化前缀匹配 → level 锁定（编 L1/章 L2/节 L3）。
2. **形状栈**（通用先验 + 锚点标定）：编号形状（第X章/一、/（一）/1. …）
   的相对深度全书稳定。排名键：锚定形状取 TOC 真实 level，
   未锚定形状按通用编号次序外推（锚定 level + 0.5 + 微偏移）。
   经典大纲栈推理：同形同级（兄弟替换）、新深形 +1（嵌套）、浅形回弹出栈。

### 验证（民法总论前 186 页分块记录）

| 层级 | 上游 bias 校正 | 本方案 |
|------|--------------|--------|
| 第一章/第二章/第三章 | L3/L5/L8（漂移） | L2/L2/L2 ✓ |
| 第一节（各章） | L4~L9 | L3 ✓ |
| 一、 | L5~L10 | L4 ✓ |
| （一） | L6~L11 | L5 ✓ |
| 1. | L7~L13 | L6 ✓ |
| (1) | L8~L14 | L7 ✓ |

90 个连续标题窗口全部正确。锚点仅命中 21 个（DeepSeek 提取的目录不全），
其余全靠形状栈正确外推。深度标题栈与硬编码的区别：层级值来自 TOC 真值，
编号形状是跨语言通用模式（12 种），不含任何书本特定规则。

### 后续修正（2026-07-19 同一天）

- **单括号编号**：`1）` `（一` 等缺左/右括号的形式，编号模式改为可选括号
- **目录页密度检测**：标题密度 ≥6/页 且在前 25% → 目录页。DeepSeek 只采样
  书首 15 页，12 页长详目的尾部会漏进正文（民法总论实测抓到 P16-19 泄漏）
- **spine/partition 由 TOC 形状定**：`_spine_from_toc`（编/章用词 + toc level
  绝对真值）替代按正文顶层标题猜测——切片里没有顶层标题时猜测会崩
- **断点续跑**：`--resume`，每个分块算完即写盘，黑屏/崩溃后从断点继续
  （笔记本 GPU 持续满载不稳，民法总论连崩两次后的刚需）
- **单块重试**：`popo_generate` 3 次重试 + `torch.cuda.empty_cache()`

### 方法论教训（用户指出）

技术验证一律切片跑（--max-pages N），不要全量跑；分块原始记录在
popo_raw/ 落盘后即可分析，不必等全流程。

---

## 五、Hybrid 引擎（stage2_hybrid.py，2026-07-19）

**动机**：Popo 本地 4B 在用户笔记本上 1.5h/本且两次搞崩显卡，
用户决定弃用本地 GPU 路线。

**设计**：与 Popo 引擎共用全部框架（popo 包的候选筛选/prompt/解析器/
表格合并/文档树/锚点校正），仅把模型调用从本地 VLM 换成 DeepSeek 云端
文本推理（4 线程并发）。不传页面图像（DeepSeek 文本接口），
四项任务全部文本化。层级仍由 TOC 锚点 + 形状栈确定——
LLM 只回答局部问题，代码拼装全局结构。

### A/B 对比（民法总论前 60 页切片，2026-07-19）

| | Popo 引擎 | Hybrid 引擎 |
|--|-----------|-------------|
| 标注来源 | 本地 4B VLM（看页图） | DeepSeek 云端（纯文本，4 线程） |
| 耗时 | ~50 min（续跑 18 min） | **23 s** |
| 标题数 | 147 | 147（一致） |
| 锚点命中 | 14 | 20 |
| EPUB 目录 | 编/章/节正确 | 与 Popo 完全一致 |
| 资源 | GPU 100%，两崩黑屏 | 零本地算力，~$0.005 |

**结论：Hybrid 成为默认引擎**（pipeline 默认 `--engine hybrid`）。
Popo 保留为离线选项。注意点：DeepSeek 不认识 popo 微调时代的
`<|id|>N<|level|>M` 输出约定，首轮全部返回分析散文——
必须用 system prompt 显式规定输出格式（_SYS_CONTD/_SYS_TITLE/
_SYS_IMAGE/_SYS_TABLE）。

**全书验证（民法总论 561 页，2026-07-19）**：76 秒、约 $0.02、7.2MB EPUB。
15/15 章全部正确、节目录嵌套全部正确、跨页拼接正常、思考题/列表项
正确留在正文、六编分隔页全部就位。配套修复：锚点标题救援（正文块精确
匹配目录条目→强制晋升标题，含"块是锚点尾部"匹配）、重复章/编合并
（相邻单元标题互为包含即合并）、编形 header 捞回（convert）。

**全书验证（高等数学·上册 442 页，2026-07-19）**：MinerU 321s + Hybrid 74s。
七章节目录嵌套全对（含打星号选学节）、习题编号全部正确留在正文、
脚注 38 条、10148 个公式转 MathML（latex2mathml，alttext 兜底）。
配套修复：标点转换跳过 $…$ / $$…$$ 公式段；脚注内公式同转 MathML。

**全书验证（The German Ideology 592 页英文书，2026-07-19）**：MinerU 521s +
Hybrid 70s。language 正确判为 en（不做中文标点转换）；Stirner 部分
"旧约/新约"的戏仿结构全部还原；脚注 731 条（$^{a}$ 字母标记），
298 个锚点链接 + 91 个尾注区。
配套修复：脚注编号解析支持 $^{a}$/a./a) 形式；字母/数字标记只允许
上标匹配；尾注正文剥离 $^{a}$ 前缀。

**全书验证（The Unlikely Pilgrimage of Harold Fry 355 页英文小说，2026-07-19）**：
MinerU 437s + Hybrid 28s + 翻译 411s。**无目录小说**的结构推断成立
（25 个章单元全靠形状栈，无锚点）；全书 1986 条译文零英文残留，
章题全译（"哈罗德、加油站女孩与信念问题"），书名译为
《哈罗德·弗莱的意外朝圣之旅》；译名表 323 条保全书一致。
配套机制：失败批次拆半递归自救；translations.json 每批落盘可断点续翻。

---

## 六、Stage 4 翻译（stage4_translate.py，2026-07-19）

- **分批语境翻译**：按阅读顺序组 ~6000 字符批次，附前一条作上下文，
  并发 4 批；prompt 注入书名/作者 + 滚动译名表（DeepSeek 每批回填），
  人名地名全书一致。失败批次拆半递归，再败保留原文。
- **应用**：译文按 source_id 映射回 blocks（stage3 的标题/正文/图注/
  脚注/前后页全部走译文）；书名译文进 metadata，EPUB 文件名即中文名。
- **断点续翻**：每批落盘 translations.json，崩溃后重跑只补缺口。
七章节目录嵌套全对（含打星号选学节）、习题编号全部正确留在正文
（"1. 求下列函数…"判为正文，"1. 映射概念"判为标题——列表项与标题的
区分 DeepSeek 判定准确）、脚注 38 条、**10148 个公式转 MathML**
（latex2mathml，失败 2 个退回 LaTeX，alttext 带源码兜底，
EPUB manifest 加 mathml properties）。
配套修复：标点转换跳过 $…$ / $$…$$ 公式段（防半角括号被转全角）。

**重页与脚注（2026-07-19 同日）**：
- 源 PDF 重页检测丢弃（8 字 shingle Jaccard>0.8，民法总论 P555/P557 实锤
  同一页被扫描两次，曾导致整章重复）
- 页脚注恢复：MinerU 的 page_footnote（全书 874 条）保留 → 正文
  $<sup>①</sup>$ 锚点匹配 → 章末尾注渲染（noteref 链接 + 回链），
  未锚定的兜底挂章尾，绝不丢内容
- 封面元数据：改 `book.set_cover()`（cover-image property + NCX meta），
  原先手动 EpubItem 缺元数据导致阅读器不认

成本估算：全书 ~140 个分块调用，输入 ~35 万字符，约 $0.02-0.05/本；
无本地算力要求，无崩溃面。

---

（以下为 v3 历史文档，仅作参考，不再执行）

---

# Books_Converter 改造计划 v3

> 从"为《民法总论》硬编码"到"适用于任意书籍"的通用化重构。
> 基于：源码逐行审阅 + pdf-craft 算法分析 + 多轮讨论。

---

## 〇、核心设计理念

**MinerU 只是 OCR 工具。** 它给的是文本和位置信息，不承担任何结构判断。
**Stage 2 完全忽略 MinerU 的 `text_level`。** 把 MinerU 输出当作一份 `.txt`，纯语义判断结构。
**LLM 成本可忽略。** DeepSeek Flash 约等于免费，时间才是瓶颈。

---

## 一、当前架构诊断

### Stage 2 硬编码问题

| 位置 | 问题 | 影响 |
|------|------|------|
| `SYSTEM_PROMPT` | "你是一位资深**中文**图书编辑" | 英文/德文/法文书完全不适用 |
| `_extract_level_mapping()` | 写死 "编/篇/卷/部""章""节" 关键词匹配 | 英文 Part/Chapter/Section、数字编号无法映射 |
| `_collect_heading_candidates()` | 正则只匹配 `一、`、`（一）`、`(1)` 等中文编号 | 英文标题、split block 全部漏掉 |
| Pass 1 | 全书塞给 DeepSeek 输出完整 structure.json | token 浪费大，准确率有限 |
| Pass 2 | 碎片化候选发给 DeepSeek（无上下文） | DeepSeek 看到的是零散碎片，无法判断 |

### Stage 3 硬编码问题

| 位置 | 问题 | 影响 |
|------|------|------|
| `_determine_spine_level()` | "第二小 level = 章" 启发式 | 只有一层的书选错 spine |
| 渲染逻辑 | 写死 "编→分隔页, 章→spine, 节→h3" | 应该 level→h-tag 映射 |
| 中文标点转换 | 硬编码在 Stage 3 里 | 应由 Stage 2 的 language 驱动 |
| TOC 嵌套 | 只有 2 层 | 应动态最多 3 层 |
| 第 300-301 行 | 死代码 `return 0` | 清理 |

---

## 二、pdf-craft 怎么做的（源码分析）

已 clone 到 `F:\MyProjects\pdf-craft\`，逐文件阅读了 `toc/toc_levels.py`、`toc/analysing.py`、`toc/toc_pages.py`、`sequence/jointer.py`、`common/statistics.py`。

### pdf-craft 的核心算法

1. **目录页检测**（`toc_pages.py`）：Aho-Corasick 多模式匹配——把所有标题文本注册为子串，扫描每页正文。标题大量出现在其他页 → 该页是目录页。**这个很巧妙，可以借鉴。**

2. **标题层级检测**（`toc_levels.py`）：**字号聚类**（`split_by_cv`，变异系数法）。收集所有 title block 的 bbox 高度，按高度聚类。最大 = level 0，次大 = level 1...**本质上就是字号检测。**

3. **段落合并**（`jointer.py`）：跨页段落拼接，英文 hyphenation 处理。

### 为什么 pdf-craft 的方案不适合我们

- 它依赖 DeepSeek OCR 的 XML 输出（有精确的 bbox 坐标），MinerU 的 JSON 虽然也有 bbox，但**我们的核心信念是不依赖视觉信号**
- 字号聚类在 OCR 不完美时会崩溃（你说的问题：同一级标题字号不同）
- 它只处理了"目录中出现的标题"，没有处理正文中的小标题（一、（一）等）

**结论：pdf-craft 的目录页检测思路可以借鉴，标题层级检测不适合我们。**

---

## 三、新方案：仿生语义分析

### 设计原则

人类怎么判断一本书的结构？

1. **翻目录** → 建立全局心智模型（这本书有几层？编>章>节？还是章>节？）
2. **逐页看** → 带着心智模型，只需局部上下文就能判断当前标题是几级
3. **看到不认识的模式** → 前后翻几页看看上下文，推断层级

**我们不需要读完全书再重写一遍，也不需要写正则去筛选。我们带着目录的心智模型，边走边标注。**

### 新 Stage 2 架构：4 步

```
Step 1: 定位目录页 → 提取目录文本
Step 2: 目录 → 心智模型（层级地图）
Step 3: 逐页行走 → 就地标注标题层级
Step 4: 输出 enhanced content_list
```

#### Step 1：定位目录页

**方法 A：借鉴 pdf-craft 的 Aho-Corasick 思路（本地代码）**

```python
# 1. 从 content_list 收集所有"疑似标题"的 block
#    条件宽松：type=text + 文本短（≤50字符）+ 有 text_level（不管值）
# 2. 用这些标题去扫描每页的全文
# 3. 如果某页包含大量"也出现在其他页"的标题 → 该页是目录页
# 4. 加约束：目录页通常在前 20% 的页码范围内
```

**方法 B：简单粗暴（备选）**

```python
# 直接把前 15 页的文本发给 DeepSeek：
# "以下是一本书的前 15 页文本，请找出目录页是哪几页"
# DeepSeek 一看就知道。成本极低（几页文本）。
```

**推荐：方法 B。** 简单、准确、跨语言通用。方法 A 作为 fallback。

#### Step 2：目录 → 心智模型

**只把目录页的文本发给 DeepSeek**（几页文本，token 极低）。

Prompt：
```
你是一位专业的图书结构分析师。以下是一本书的目录页文本。

请分析并输出：
1. **language**：这本书的主要语言（zh/en/de/ja/fr...）
2. **has_toc**：这确实是目录吗？（true/false）
3. **level_map**：这本书使用了哪些层级？每层的特征是什么？
   - 不要假设特定术语。用 level 数字（1, 2, 3...）表示。
   - 给出每层的编号模式描述和示例。
4. **toc_entries**：列出所有目录条目（标题 + 页码 + 层级）
5. **metadata**：书名、作者、出版社

输出 JSON：
{
  "metadata": {"title": "...", "authors": [...], "language": "zh", "publisher": "..."},
  "has_toc": true,
  "level_map": {
    "1": {"description": "编（如'第一编 民法概述'）", "pattern": "第X编", "count": 3},
    "2": {"description": "章（如'第一章 民法的概念'）", "pattern": "第X章", "count": 15},
    "3": {"description": "节（如'第一节 民法的定义'）", "pattern": "第X节", "count": 42}
  },
  "toc_entries": [
    {"text": "第一编 民法概述", "level": 1, "page": 9},
    {"text": "第一章 民法的概念", "level": 2, "page": 9},
    ...
  ]
}
```

**输出：`level_map`（心智模型）+ `toc_entries`（目录条目列表）**

#### Step 3：逐页行走 → 就地标注

这是核心创新点。**不重写全书，不批量处理碎片，而是逐页遍历 content_list，就地标注。**

```python
def annotate_headings(content_list, level_map, toc_entries):
    """
    逐页遍历 content_list，对每个 block 判断是否为标题，就地标注 heading_level。
    
    状态变量：
    - current_chapter: 当前所在的最近 TOC 章节（标题 + level + page）
    - 用于给 LLM 提供上下文
    """
    
    # 构建 TOC 查找表（归一化文本 → toc_entry）
    toc_lookup = build_toc_lookup(toc_entries)
    
    # 状态
    current_context = {
        "nearest_toc": None,  # 最近的 TOC 章节
        "nearest_toc_level": 0,
    }
    
    enhanced_blocks = []
    
    for i, block in enumerate(content_list):
        text = block.get("text", "").strip()
        
        # 快速跳过：明显不是标题的 block
        if _obviously_not_heading(block, text):
            enhanced_blocks.append(block)
            continue
        
        # 第一层判断：文本归一化后与 TOC 条目匹配？
        normalized = normalize(text)
        if normalized in toc_lookup:
            # 匹配上了！直接赋值 level，不需要 LLM
            toc_entry = toc_lookup[normalized]
            block = {**block, "heading_level": toc_entry["level"], "in_toc": True}
            # 更新上下文
            current_context["nearest_toc"] = toc_entry
            current_context["nearest_toc_level"] = toc_entry["level"]
            enhanced_blocks.append(block)
            continue
        
        # 第二层判断：编号模式匹配（纯本地）
        pattern_level = _match_numbering_pattern(text, level_map)
        if pattern_level is not None:
            block = {**block, "heading_level": pattern_level, "in_toc": False}
            enhanced_blocks.append(block)
            continue
        
        # 第三层判断：LLM 兜底（只在需要时调用）
        # 收集局部上下文：前后各 3 个 block + 当前章节信息
        context = _build_local_context(content_list, i, current_context)
        
        llm_result = _ask_llm_is_heading(text, context, level_map)
        if llm_result and llm_result.get("is_heading"):
            block = {**block, 
                     "heading_level": llm_result["level"], 
                     "in_toc": False}
        
        enhanced_blocks.append(block)
    
    return enhanced_blocks
```

**三层判断策略：**

| 层级 | 方法 | 成本 | 覆盖率 |
|------|------|------|--------|
| 1 | TOC 文本匹配 | 零（本地） | ~30%（目录中出现的大标题） |
| 2 | 编号模式匹配 | 零（本地） | ~40%（`4.2.2.1`、`一、`、`（一）`等） |
| 3 | LLM 局部判断 | 极低（几 token） | ~30%（边界情况） |

**关键：LLM 只看到局部上下文（前后 3 个 block + 当前章节），不是全书。**

```python
def _ask_llm_is_heading(text, context, level_map):
    """
    问 LLM 一个简单问题：这个 block 是标题吗？几级？
    
    给 LLM 的信息：
    - 当前文本
    - 前后各 3 个 block 的文本（提供上下文）
    - 这本书的 level_map（心智模型）
    - 当前所在的最近 TOC 章节
    """
    prompt = f"""你正在标注一本书中的标题层级。

书籍层级结构：
{format_level_map(level_map)}

当前位置：你正在「{context['nearest_toc']['text']}」(level {context['nearest_toc_level']}) 这一节内。

上下文（前后各3个block）：
---
[前3] {context['before_3']}
[前2] {context['before_2']}
[前1] {context['before_1']}
[当前] >>> {text} <<<
[后1] {context['after_1']}
[后2] {context['after_2']}
[后3] {context['after_3']}
---

问题：「{text}」是标题吗？
- 如果不是标题：{{"is_heading": false}}
- 如果是标题：{{"is_heading": true, "level": N}}
  level 必须 > {context['nearest_toc_level']}（子标题层级比父标题深）

只输出 JSON。"""

    # 调用 DeepSeek Flash（极快，极便宜）
    ...
```

#### Step 4：输出 enhanced content_list

不再输出独立的 `structure.json`。改为直接在 content_list 的每个 block 上添加字段：

```json
{
  "type": "text",
  "text": "一、公、私法的划分",
  "page_idx": 44,
  "heading_level": 4,
  "in_toc": false
}
```

Stage 3 读这些标注来渲染。

### 编号模式匹配的设计

```python
def _match_numbering_pattern(text, level_map):
    """
    通用编号模式匹配。
    
    关键：不是写死中文正则，而是用 level_map 中的 pattern 动态生成正则。
    
    例如 level_map 说 level 1 的 pattern 是 "第X编"，
    则生成正则 r"第[一二三四五六七八九十]+编"。
    
    通用模式（始终启用）：
    - 纯数字嵌套：^\d+(\.\d+)+ → 4.2.2.1 = level 4
    - 罗马数字：^[IVX]+\. → level 视上下文
    """
    
    # 1. 纯数字嵌套（跨语言通用）
    m = re.match(r"^(\d+(?:\.\d+)+)", text)
    if m:
        depth = m.group(1).count(".") + 1  # 4.2.2.1 → depth 4
        return depth
    
    # 2. 从 level_map 动态生成模式
    for level, info in level_map.items():
        pattern_desc = info.get("pattern", "")
        regex = _pattern_to_regex(pattern_desc)
        if regex and re.match(regex, text):
            return int(level)
    
    # 3. 通用中文子标题模式（始终启用作为 fallback）
    chinese_patterns = [
        (r"^[一二三四五六七八九十]+、", "zh_sub_1"),
        (r"^（[一二三四五六七八九十]+）", "zh_sub_2"),
        (r"^\d+\.\s", "num_dot"),
    ]
    for pattern, name in chinese_patterns:
        if re.match(pattern, text):
            # 返回相对于当前 TOC 章节的下一级
            return None  # 让 LLM 判断具体 level
    
    return None
```

### 没有目录的书

Step 1 判断 `has_toc: false` 后：
- `level_map` 和 `toc_entries` 为空
- Step 3 中第一层（TOC 匹配）和第二层（编号模式）可能都匹配不上
- 更多依赖 LLM 判断，但仍然用局部上下文（前后 3 个 block）
- DeepSeek 从上下文中的编号模式、排版特征仍然能推断层级

### 成本估算

| 步骤 | Token | 成本 |
|------|-------|------|
| Step 1 定位目录 | ~5K in + ~200 out | ~$0.001 |
| Step 2 目录→心智模型 | ~3K in + ~1K out | ~$0.001 |
| Step 3 LLM 兜底（~100次调用） | ~500 in + ~50 out × 100 | ~$0.01 |
| **合计** | | **~$0.01/本** |

比当前方案的 $0.03-0.08 更便宜，而且更准确（因为有上下文）。

---

## 四、Stage 3 改造

### Level-driven 渲染

```
level → h-tag:   level N → hN（纯数字映射）
level → EPUB:    由 Stage 2 的 level_map 决定 spine 层
```

### 动态 spine 确定

```python
def determine_spine_level(level_map, toc_entries):
    """
    从 level_map 确定哪一层是 spine（独立章节）。
    
    策略：
    - level_map 中 count 最少的那一层 → 分隔页（如"编"只有3个）
    - count 次少的那一层 → spine（如"章"有15个）
    - 更深层 → 章内子标题
    """
    levels = sorted(level_map.items(), key=lambda x: x[1]["count"])
    if len(levels) >= 2:
        partition_level = int(levels[0][0])  # count 最少 = 最高层
        spine_level = int(levels[1][0])       # count 次少 = spine 层
    elif len(levels) == 1:
        partition_level = None
        spine_level = int(levels[0][0])
    else:
        partition_level = None
        spine_level = 1
    
    return partition_level, spine_level
```

### TOC 嵌套

最多 3 层，动态适应：

```python
unique_levels = sorted(set(e["level"] for e in toc_entries))
toc_depth = min(3, len(unique_levels))
toc_levels = unique_levels[:toc_depth]

# 构建树：
# level[0] → epub.Section(parent)
#   level[1] → epub.Link(child)  
#     level[2] → epub.Link(grandchild)
```

超过 toc_depth 的标题：不进入 TOC 导航栏，但在正文中渲染为 h4/h5。

### 语种参数化

```python
# Stage 2 输出 metadata.language
# Stage 3 读取：
if metadata.get("language") == "zh":
    text = convert_punctuation(text)  # 英文标点 → 中文标点
# 其他语种不做转换
```

---

## 五、文件路径规范

```
D:\My_Library\
  民法总论\
    民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk).pdf   ← 源文件
    民法总论.epub                                               ← 最终产物（clean title）
    民法总论 (杨代雄) (z-library.sk, 1lib.sk, z-lib.sk)\        ← 工作目录（PDF stem）
      mineru\
        *.md                    ← MinerU Markdown
        *_content_list.json     ← MinerU block 数据
        metadata.json           ← MinerU 元数据
        images\                 ← 图片
      structure.json            ← Stage 2 输出（level_map + toc_entries + enhanced blocks）
      cover.jpg                 ← 封面
```

---

## 六、Stage 2 数据结构

```json
{
  "metadata": {
    "title": "民法总论",
    "authors": ["杨代雄"],
    "language": "zh",
    "publisher": "北京大学出版社",
    "has_toc": true,
    "toc_page_range": [5, 8]
  },
  "level_map": {
    "1": {"description": "编", "pattern": "第X编", "count": 3},
    "2": {"description": "章", "pattern": "第X章", "count": 15},
    "3": {"description": "节", "pattern": "第X节", "count": 42}
  },
  "toc_entries": [
    {"text": "第一编 民法概述", "level": 1, "page": 9},
    {"text": "第一章 民法的概念", "level": 2, "page": 9},
    ...
  ],
  "enhanced_blocks": [
    {"type": "text", "text": "第一编 民法概述", "page_idx": 8, "heading_level": 1, "in_toc": true},
    {"type": "text", "text": "一、公、私法的划分", "page_idx": 44, "heading_level": 4, "in_toc": false},
    {"type": "text", "text": "自然人因出生而取得权利能力...", "page_idx": 44},
    ...
  ]
}
```

---

## 七、实施计划

### Phase 1：新 Stage 2（核心，预计 2-3 天）

- [ ] Step 1：目录页定位（LLM 判断前 15 页）
- [ ] Step 2：目录 → 心智模型（LLM 提取 level_map + toc_entries）
- [ ] Step 3：逐页行走标注（TOC 匹配 + 编号模式 + LLM 兜底）
- [ ] Step 4：输出 enhanced content_list
- [ ] 去掉所有中文硬编码

### Phase 2：Stage 3 重构（预计 2 天）

- [ ] level-driven 渲染（h-tag 映射）
- [ ] 动态 spine 确定（基于 level_map count）
- [ ] 动态 TOC 嵌套（最多 3 层）
- [ ] 语种参数化标点转换
- [ ] 清理死代码

### Phase 3：Pipeline 优化（预计 1 天）

- [ ] EPUB 用 clean title 命名
- [ ] 缓存策略
- [ ] 错误降级

### Phase 4：测试（预计 2 天）

| 书 | 特征 | 验证重点 |
|----|------|---------|
| 民法总论 | 中文，编/章/节/小节，有 TOC | 多层级嵌套、中文标点 |
| 高等数学 | 中文，章/节，大量公式 | 公式渲染、无"编"层 spine |
| 德意志意识形态 | 英文，Part/Chapter/Section | 英文标点不转换、英文层级 |
| 城市与国家财富 | 中文翻译本 | 翻译本结构 |

---

## 八、To-Do List

### P0 — 当前改造

- [ ] Stage 2 完全重写（仿生语义分析）
- [ ] Stage 3 level-driven 渲染
- [ ] Stage 3 动态 spine + TOC 嵌套
- [ ] 语种参数化

### P1 — 近期增强

- [ ] 全书翻译（Stage 4）
- [ ] 公式增强（LaTeX → MathML/SVG）
- [ ] 多模型支持

### P2 — 可选

- [ ] 批量转换 CLI
- [ ] 转换质量报告

---

## 九、与 pdf-craft 方案的对比

| | pdf-craft | 我们（新方案） |
|--|-----------|---------------|
| 标题检测 | 字号聚类（bbox 高度） | 纯语义（TOC 匹配 + 编号模式 + LLM） |
| 目录定位 | Aho-Corasick 子串匹配 | LLM 直接看前几页（更简单准确） |
| LLM 使用 | 可选，用于复杂章节分析 | 核心驱动，但只处理局部上下文 |
| 成本 | 低（纯本地统计） | ~$0.01/本（几乎免费） |
| 跨语言 | 依赖 OCR 字号精度 | 天然支持（语义不区分语言） |
| split block | 无处理 | LLM 带上下文可以识别 |
| 无 TOC 的书 | 回退到字号聚类 | LLM 从正文结构推断 |
