# Wiki 索引 — Books_Converter

> 面向维护者的管线语义文档。用户向用法见 `README.md`；立项架构动机见
> `PROJECT.md`；逐病例的翻车与修复档案见 `FIXLOG.md`。
> 改动规则（强制验证链/书库隔离/收尾义务）见根目录 `AGENTS.md`。

| 页 | 内容 |
|---|---|
| `01-architecture.md` | 四级管线与数据契约：Stage 1 解析引擎 → Stage 2 结构重建 → Stage 3 EPUB 装订 → Stage 4 翻译 |
| `02-structure-system.md` | **核心**：锚点与层级语义系统——toc_entries、锚点匹配规则、形状栈、救援/下沉/查重/否决器、泛名书签位置锚定、spine 检测、render 切章 |
| `03-verification.md` | 测试套件、真书回归、QC 体检、暂存副本与书库隔离、产物亲读清单 |
| `04-cases.md` | FIXLOG 病例分类导读（按缺陷类别索引，链接回 FIXLOG 条目） |
| `05-build-release.md` | 版本号、PyInstaller 打包、SageRead sidecar 部署、gitignore 卫生 |

## 三分钟上手

```bash
# 跑一本书（强制 OCR + 译成中文）
.venv/Scripts/python.exe pipeline.py "D:\path\book.pdf" --engine paddleocr --headless --translate zh

# 复用 Stage 1 缓存快速迭代结构/装订
.venv/Scripts/python.exe pipeline.py "D:\path\book.pdf" --engine paddleocr --headless --skip-mineru

# 体检产物
.venv/Scripts/python.exe qc_book.py "D:\path\book\book"
```

## 本仓库的复杂度集中在哪

不在引擎，不在 EPUB 打包，而在 **Stage 2 的层级语义**：目录条目是绝对的
结构真值，正文标题是噪声候选，两者之间由一套"锚点匹配 + 形状栈 +
救援/下沉 + 查重/否决"的规则系桥接。这套规则的全部守卫都必须满足
"失败方向=不动作"。改之前先读 `02-structure-system.md`。
