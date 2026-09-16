import { describe, expect, it } from "vitest";
import { EMPTY_KINDS } from "../../components/EmptyState";
import { S } from "../strings";

describe("空态组件", () => {
  it("三个 kind 齐全且文案非空", () => {
    expect(EMPTY_KINDS).toEqual(["queue", "library", "notifications"]);
    for (const k of EMPTY_KINDS) {
      expect(S.empty[k].title.length).toBeGreaterThan(0);
      expect(S.empty[k].hint.length).toBeGreaterThan(0);
    }
  });
});

describe("strings 完整性（i18n 收编后的关键键）", () => {
  it("errors 人话标题", () => {
    expect(S.err.invalidKey).toBe("密钥无效或未开通服务");
    expect(S.err.rateLimited.includes("429")).toBe(false); // 人话，不带原始码
    expect(S.err.network).toContain("网络");
  });

  it("precheck 模板", () => {
    expect(S.precheck.postModelKey("deepseek-chat")).toContain("deepseek-chat");
    expect(S.precheck.vlmModelKey("glm-5.3-flash")).toContain("glm-5.3-flash");
  });

  it("再次转换/队列日志/缩放文案", () => {
    expect(S.library.toastReconvert).toContain("队列");
    expect(S.library.sourceMissing("F:/a.pdf")).toContain("F:/a.pdf");
    expect(S.convert.logStart("书", "vlm")).toContain("书");
    expect(S.convert.stageProgress(1, 3, "VLM")).toContain("1/3");
    expect(S.settings.zoomHint(110)).toContain("110%");
  });
});
