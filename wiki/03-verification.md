# 03 验证：测试、回归、QC、亲读

> 规则原文见根目录 `AGENTS.md` 强制验证链。本页是怎么做。

## 单元测试（tests/，裸 python 可跑）

| 文件 | 覆盖 |
|---|---|
| `test_structure_rescue.py` | 结构语义回归主套件：截断/退化锚/模糊匹配正反例/level=0/救援位置闸门/锚点查重三明治/垃圾否决器/泛名书签位置锚定与层级重建/正文起点回收/Problem 前缀剥离/spine 词形 |
| `test_stage2_toc.py` | 目录页码重配、页码救援放弃条件、运行头收敛等（病例 001/003 系） |
| `test_stage3_merge.py` | 段落合并信号制（病例 018） |
| `test_stage3_promote.py` | 孤公式显示升级 |
| `test_stage4_translate.py` | 翻译批响应全覆盖校验（病例 020） |

跑法：`.venv/Scripts/python.exe tests/<文件>`（或 `pytest tests/`）。
**新增守卫必须带正反例用例。**

## 真书回归（暂存副本）

- `_regress/`（已 gitignore）放常驻暂存副本：PDF + `<书名>/`（引擎缓存
  工作目录）。重跑：`pipeline.py <暂存PDF> --engine <原引擎> --headless
  --skip-mineru`（stage2+stage3 一两分钟）。
- 书目按改动面选：Feeling Great（印刷目录+worksheet 噪声）、
  刘擎西方现代思想讲义（多 regime 页码、附录系列）、伊豆の踊子（无目录页
  纯形状栈）、Born a Crime（具名书签+超长章题）、Condensed Matter（泛名
  书签）、QFT（多级编号+章首 mini-TOC+公式）、German Ideology（千页无
  目录德文）。
- **绝对禁止**在 `D:\My_Library` 既有书籍目录里就地重跑（会覆盖用户的
  EPUB）。新书建档目录除外（该书自己的构建区）。
- 批量串行+体检汇总：`run_batch.py --engine paddleocr <pdfs...>`。

## QC 体检（qc_book.py）

```bash
.venv/Scripts/python.exe qc_book.py <work_dir> [...]
```

红：内容丢失（content_list 非噪声块未进 popo blocks）/ 目录条目未锚上
正文标题（含 `_pos_anchor` 位置锚定命中）/ 幻影合成标题（保守，合法合成
也会报，读位置自行判断）/ 章节标题无目录条目（反向校验，病例 019 的
"锚定全绿但成品缺章"盲区）。
黄：数字编号标题无目录条目（小节编号常见，看量）、重复标题、空章、
降级 metadata、无目录条目。

## 产物亲读清单（QC 绿之后必做）

1. 解包 EPUB 读 `EPUB/nav.xhtml`：层级嵌套是否与书的目录一致；垃圾条目
   （填空行/表格单元/幻觉词/索引字母/fnref_*）清零。
2. 抽查章首：章标题是否在章首句**之前**（"电影开场十分钟才标开头"即
   病例 023 的运行头顶替）；章首段落是否完整进章。
3. 抽查双目录：同章是否出现编号版+无编号版两条。
4. 抽查分区：Part/Chapter 嵌套是否符合书的真实结构。
5. 翻译书另查：章题译文是否与内容对位（译文错位病例 020）。
