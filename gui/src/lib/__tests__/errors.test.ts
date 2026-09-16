import { describe, expect, it } from "vitest";
import { humanizeError } from "../errors";

describe("humanizeError 人话映射", () => {
  it("401/403 → 密钥无效或未开通服务", () => {
    expect(humanizeError('HTTP Error 401: {"error":"unauthorized"}').title).toBe(
      "密钥无效或未开通服务",
    );
    expect(humanizeError("Error code: 403 - forbidden by provider").title).toBe(
      "密钥无效或未开通服务",
    );
  });

  it("429 → 限流", () => {
    expect(humanizeError("HTTP 429 Too Many Requests").title).toBe(
      "触发限流，稍后再试或降低并发",
    );
    expect(humanizeError("Rate limit reached for rpm").title).toBe(
      "触发限流，稍后再试或降低并发",
    );
  });

  it("超时/网络错 → 网络连接失败", () => {
    expect(humanizeError("requests.exceptions.ConnectTimeout: timed out").title).toBe(
      "网络连接失败，检查网络或代理",
    );
    expect(humanizeError("HTTPSConnectionPool: Connection refused").title).toBe(
      "网络连接失败，检查网络或代理",
    );
  });

  it("未收录报错 → 首行截断 + 原文进 detail", () => {
    const raw = "RuntimeError: vlm_state.db 损坏\n  at line 3\n  at line 4";
    const h = humanizeError(raw);
    expect(h.title).toBe("RuntimeError: vlm_state.db 损坏");
    expect(h.detail).toBe(raw);
  });

  it("长首行截断到 60 字符", () => {
    const h = humanizeError("x".repeat(100));
    expect(h.title.length).toBe(61); // 60 + …
  });

  it("空输入不炸", () => {
    expect(humanizeError("").title).toBe("未知错误");
  });
});
