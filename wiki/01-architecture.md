# 01 架构与数据契约

## 四级管线

```
PDF ──▶ Stage 1 解析引擎（云端 OCR/版面分析）
        ├─ mineru（MinerU 云，支持 强制/自动 OCR；自动模式对 born-digital 用文本层）
        ├─ paddleocr（PaddleOCR-VL 云，永远整页识别；ocr 开关被忽略）
        └─ vlm（多模态模型逐页直读，stage1_vlm：GLM 转写+脚注重建、
                同模型图片粗框+光栅重裁、SQLite 状态断点续跑、目录先验；
                调研/实验/选型见 wiki/06，T0–T6 全绿）
        产出（落盘 <work_dir>/<engine>/）：
          {stem}_content_list.json   MinerU 风格块列表（契约）
          {stem}.md                  引擎直出 markdown（留档）
          images/                    裁切好的图片
          ──▶ Stage 2 结构重建（按引擎分流）
                ├─ 规则引擎 → stage2_hybrid + stage2_common：
                │    LLM 分块标注（contd 跨页拼接 / 标题层级 / 图文关联 / 跨页表格）
                │    + 锚点层级系统（见 02）+ 轻量兜底（metadata/前后页/目录条目）
                └─ vlm → stage2_vlm（薄编排，设计稿 wiki/09）：本地映射
                     （text_level/图文配对直连）+ 规则版 contd + 目录先验直接
                     作锚 + 共享匹配器/形状栈/降格守卫 + 交叉校验报告；
                     无 DeepSeek 重打标、无目录检测、无救援合成
                产出：popo_blocks.json + structure.json
          ──▶ Stage 3 EPUB 装订（stage3_epub）
                线性切章 → 嵌套 nav → MathML/尾注/封面
                产出：根级 <书名>.epub → _deliver 复制进 epub/
                └─ 平行导出（stage3_export，--format epub,md,tex 多选）：
                   复用 _render_popo_body 单元判定，HTML parts →
                   Markdown（GFM/Pandoc、单文件/分章）与 TeX（xelatex 完整
                   文档/片段）；公式经 alttext 回收 LaTeX，图片共用 images/
          ──▶ Stage 4 翻译（可选，stage4_translate，--translate zh）
                DeepSeek 分批+上下文+译名表；translations.json 断点续翻
```

### 产物目录契约（病例 049 起）

`<输出目录>/<书名>/`（work_dir 兼任缓存根与交付根）终态只留交付物 + 缓存：

```
├── epub/<书名>.epub   电子书成品
├── md/                Markdown（index.md + chapters/ 分章 + images/）
├── tex/               TeX + images/（默认完整文档可 xelatex 直接编译，
│                      首行 % !TeX program = xelatex magic comment 防
│                      pdflatex 误编译；片段模式头部带「不能直接编译」
│                      注释警告块，见 FIXLOG 病例 056）
├── vlm/ mineru/ paddleocr/
                     Stage 1 引擎缓存（按引擎名各存一份：vlm=逐页视觉模型，
                     mineru/paddleocr=规则引擎族，三者互不通用；重跑提速，可整个删）
└── structure.json / popo_blocks.json / translations.json
                     Stage 2/4 小缓存（复跑与 qc_book 需要）
```

缓存目录**按引擎键控**（`work_dir / <engine>`，pipeline.py:630）而非按
"规则/VLM" 家族合并——同一本书换引擎 A/B 时两份缓存必须共存互踩不得
（FIXLOG 047 的 MinerU/Paddle 对照实验即依赖这一点）。

`_deliver` 全部格式复制成功后，pipeline 自动清理根级中间产物
（`<书名>.epub/.tex/.md`、`<书名>_md/`、根级 `images/`、`cover.jpg`——
它们与交付副本字节相同，且每跑必从引擎缓存/PDF 重建，重跑不依赖）。
任一格式导出/交付失败则不清理（失败方向 = 不动作）。

## LLM 调用约定

所有 chat.completions 调用必须经 `llm_thinking.chat_create` 包装（5 处：
stage2_hybrid ×1、stage2_common ×2、stage4_translate ×2）——思考参数进程级
协商：能关则关（thinking:disabled），端点拒绝（400/1210）则降
reasoning_effort=low（对齐 SageRead reasoning-map.ts 恒思考模型取最低档的
口径），仍拒则不下发思考参数。**不拦任何模型**（恒思考模型慢但可用）；
并发下旧模式请求的迟到拒绝只重发不再降档（病例 025）。

## 关键数据契约

### content_list（Stage 1 → 下游的块契约）

每块：`type`（text/title/image/table/header/footer/page_number/aside_text/
image_caption/table_caption/page_footnote…）、`text`、`text_level`
（引擎标题层级；MinerU 是真层级 1..N，PaddleOCR 只有 0/1 二值——
`stage1_layout.py` 一律打 1）、`bbox`（0–1000 千分位）、`page_idx`（0 起）。

### popo_blocks（Stage 2 的标注块，stage3 的唯一输入）

在 content_list 基础上由 `popo/convert.py` 归一化：`type`/`content`/`bbox`
（0–1 浮点）/`page`（**1 起**）/`source_id`（`{book}:{content_list索引}`，
译文查找靠它）/`source_label`/`title_level`（引擎层级，**写了无人读**）、
`id`（阅读顺序）。Stage 2 追加标注：`level`（LLM 首票→锚点校正）、
`level_raw`、`contd`/`image`/`table_merge`、`_anchored`、`rescued`、
`_pos_anchor`/`_pos_num`（泛名书签位置锚定标记）。

> ⚠️ `source_id` 必须与 `content` 同步：锚点富化/合成改写 content 时若保留
> 旧 source_id，译文会取回原文本的译文（病例 019 的幻影'抑郁'分区即
> 富化改写 + 旧 source_id 取错译文的合谋）。

### structure.json

`engine`、`metadata`、`front_matter`/`back_matter`（[{type,label,page_start,
page_end,keep}]）、`toc_entries`（[{text,level,page}]，**page 是印刷页码**；
outline 来源时是扫描页）、`toc_source`（'outline'|None——outline 页码才能
用于正文起点回收）、`tree`（仅 QC 用，stage3 不读）、`popo_blocks_file`。

## 工作目录与 CLI

`work_dir = 输出目录 / PDF词干`；`--skip-mineru` 复用 Stage 1 缓存、
`--skip-deepseek` 复用 structure.json、`--translate zh` 开 Stage 4、
`--headless` JSON 进度（sidecar 协议见 README/集成文档）。

## Stage 4 翻译要点

- 批响应**必须 1..N 全覆盖**，缺号/跳号抛错走 重试→拆半；失败方向=保留
  原文（病例 020：缺号静默收编导致整批错位并经续翻缓存扩散）。
- `translations.json` 断点续翻按 content_list 索引对齐——**引擎缓存不变
  才可续翻**；结构重跑若改变了块集合，先删 translations.json 再跑。
- 落盘形态 `{"translations": {key: 译文}, "glossary": {…}}`（嵌套）；导出层
  （stage3_export.build_units）嵌套/平铺两形兼容读取，查无译文回退原文
  （病例 061：曾按平铺直读 → md/tex 译文导出静默退回原文）。
