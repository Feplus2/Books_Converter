# AGENTS.md — Books_Converter 维护规则

> 本仓库的结构重建语义已相当复杂（锚点/层级系统见 `wiki/02-structure-system.md`）。
> 所有改动必须遵守本页规则。规则由仓库所有者（用户）制定，优先级高于任何
> 自动化判断。新人/Agent 动手前先读 `wiki/00-index.md` + `FIXLOG.md` 头部。

## 铁律 0：守卫的失败方向必须是"不动作"

每条新守卫/新匹配规则，先问：它失效时会怎样？答案必须是"宁可不做，
也不错做"（不锁、不晋升、不合并、不删内容）。绝不引入"乱动作"的守卫。
参见 FIXLOG 阅读指南与本规则在 019–024 病例中的反复验证。

## 改动后的强制验证链（缺一不可）

1. **单元测试全绿**：
   ```bash
   .venv/Scripts/python.exe tests/test_structure_rescue.py   # 结构语义回归
   .venv/Scripts/python.exe tests/test_stage2_toc.py         # 目录页/页码
   .venv/Scripts/python.exe tests/test_stage3_merge.py       # 段落合并
   .venv/Scripts/python.exe tests/test_stage3_promote.py
   .venv/Scripts/python.exe tests/test_stage4_translate.py   # 翻译批校验
   ```
   新修规则必须带新测试用例进 `tests/`。
2. **真书回归**：用暂存副本跑真实书（`--skip-mineru` 复用 Stage 1 缓存），
   书目按改动面选择（至少覆盖：有印刷目录的书、无目录书、有 PDF 书签的书、
   中文书）。`_regress/` 有常驻暂存副本与缓存。跑完必须 `qc_book.py` 过一遍。
3. **亲自读产物**：打开产出的 EPUB，逐条读 nav 层级、抽查章首归属
   （章标题是否在章首句之前）、垃圾条目是否清零。QC 全绿不算数，
   读才算数（FIXLOG 病例 019：QC 25/25 全绿时成品照样稀碎）。

## 书库隔离

- `D:\My_Library` 是用户的个人书库：**库内既有 EPUB 只读，永不覆盖**；
  库里的 PDF 可作测试输入，但测试一律先复制到 `_regress/` 暂存再跑。
- 例外：用户指定"建档"的新书目录（该书自己的构建区）可就地跑。
- 测试产物/缓存/暂存 PDF 不得入库提交（`.gitignore` 已列 `_regress/`、
  `_batch_logs/`、`output/`、`*.epub`、`.env`、`gui_settings.json`）。

## 发现问题的工作流

- 自检（QC 红灯/读产物发现异常）→ **先报告问题**（现象 + 根因定位 +
  修补建议），不要闷头改。
- 小修（窄守卫、单行词形、显示层）：报告后直接修，走强制验证链。
- 大幅改动（新阶段、新匹配范式、改动既有语义）：**获用户批准后**，
  可自主进入下一轮"实现 → 测试 → 回归 → 读产物"迭代，直至稳定。

## 收尾义务

- FIXLOG.md 登记病例（现象 → 根因链 → 修补点（文件：函数）→ 回归证据 →
  状态），编号顺延；挂账问题也要登记。
- 语义/规则变化同步更新 `wiki/` 对应页。
- 版本号只改 `version.py`；sidecar 重打与部署见 `wiki/05-build-release.md`。
- 未获用户明确要求，不做 git commit/push。
