// 病例 057：FileRow 按钮直义化——目录行只渲染 open（tooltip=打开文件夹），
// 文件行 open + reveal 双按钮（tooltip=打开文件 / 在文件夹显示）
import { describe, expect, it } from "vitest";
import { fileRowActions } from "../registry";
import { S } from "../strings";

describe("fileRowActions 产物行按钮决策", () => {
  it("目录行：只 open，tooltip=打开文件夹，不渲染 reveal", () => {
    const a = fileRowActions({ is_dir: true });
    expect(a.showReveal).toBe(false);
    expect(a.openTooltip).toBe("openFolder");
    expect(S.library[a.openTooltip]).toBe("打开文件夹");
  });

  it("文件行：open + reveal 双按钮，tooltip=打开文件 / 在文件夹显示", () => {
    const a = fileRowActions({ is_dir: false });
    expect(a.showReveal).toBe(true);
    expect(a.openTooltip).toBe("openFile");
    expect(S.library[a.openTooltip]).toBe("打开文件");
    expect(S.library.reveal).toBe("在文件夹显示");
  });

  it("旧 open 键已移除（不存在语义含糊的「打开」）", () => {
    expect("open" in S.library).toBe(false);
  });
});
