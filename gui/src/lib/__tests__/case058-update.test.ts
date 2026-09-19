// 病例 058：检查更新消息三态 + Releases 页链接恒在（失败也给手动出口）
import { describe, expect, it } from "vitest";
import { RELEASES_URL, updateResultToMsg } from "../updater";

describe("updateResultToMsg 更新检查消息", () => {
  it("已是最新：ok + 链接回退 releases 列表页", () => {
    const m = updateResultToMsg({ status: "latest" });
    expect(m.tone).toBe("ok");
    expect(m.text).toContain("最新");
    expect(m.url).toBe(RELEASES_URL);
  });

  it("发现新版本：warn + 版本号 + 直达该 tag 发布页", () => {
    const m = updateResultToMsg({
      status: "update",
      latest: "1.4.0",
      url: "https://github.com/Feplus2/Books_Converter/releases/tag/v1.4.0",
    });
    expect(m.tone).toBe("warn");
    expect(m.text).toContain("v1.4.0");
    expect(m.url).toContain("/releases/tag/v1.4.0");
  });

  it("update 态 url 解析缺失 → 兜底 releases 列表页", () => {
    const m = updateResultToMsg({ status: "update", latest: "1.4.0" });
    expect(m.url).toBe(RELEASES_URL);
  });

  it("检查失败：err 如实提示 + 仍给手动出口（失败方向=不动作+如实）", () => {
    const m = updateResultToMsg({ status: "failed", error: "timeout" });
    expect(m.tone).toBe("err");
    expect(m.text).toContain("timeout");
    expect(m.url).toBe(RELEASES_URL);
  });
});
