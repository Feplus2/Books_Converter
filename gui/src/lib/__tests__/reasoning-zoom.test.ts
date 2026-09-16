import { describe, expect, it } from "vitest";
import { migrate, reasoningSupported } from "../settings";
import { PROVIDER_PRESETS } from "../providers";

describe("reasoningSupported 思考档可见性守卫", () => {
  it("支持思考参数的端点（bigmodel/deepseek/dashscope）→ true", () => {
    expect(reasoningSupported("bigmodel/glm-5.3-flash")).toBe(true);
    expect(reasoningSupported("deepseek/deepseek-v4-pro")).toBe(true);
    expect(reasoningSupported("dashscope/qwen3.8-flash")).toBe(true);
  });

  it("其他提供商（openai/moonshot/custom…）→ false（UI 不暴露）", () => {
    expect(reasoningSupported("openai/gpt-5")).toBe(false);
    expect(reasoningSupported("moonshot/kimi-k3")).toBe(false);
    expect(reasoningSupported("custom/whatever")).toBe(false);
    // 已移除的 Anthropic 预设：未收录 id 同样 false（不暴露=不动作）
    expect(reasoningSupported("anthropic/claude-opus-5")).toBe(false);
  });

  it("未选模型（.env 兜底）→ true（保持默认行为）", () => {
    expect(reasoningSupported("")).toBe(true);
  });

  it("预设表 sanity：默认列表 10 家，无 z.ai / anthropic", () => {
    const ids = PROVIDER_PRESETS.map((p) => p.id);
    expect(ids).toHaveLength(10);
    expect(ids).not.toContain("zai");
    expect(ids).not.toContain("anthropic");
    for (const id of [
      "bigmodel",
      "deepseek",
      "dashscope",
      "moonshot",
      "openai",
      "openrouter",
      "gemini",
      "grok",
      "volcengine",
      "custom",
    ]) {
      expect(ids).toContain(id);
    }
  });
});

describe("migrate 序列化（zoom 持久化 + 旧字段）", () => {
  it("旧版 appearance 字符串 → {theme, zoom:1}", () => {
    const s = migrate({ appearance: "dark" } as never);
    expect(s.appearance).toEqual({ theme: "dark", zoom: 1 });
  });

  it("appearance 对象 zoom 保留", () => {
    const s = migrate({ appearance: { theme: "light", zoom: 1.2 } } as never);
    expect(s.appearance).toEqual({ theme: "light", zoom: 1.2 });
  });

  it("zoom 越界收敛 0.7–1.5；非法主题回 system", () => {
    expect(migrate({ appearance: { theme: "dark", zoom: 3 } } as never).appearance.zoom).toBe(1.5);
    expect(migrate({ appearance: { theme: "dark", zoom: 0.1 } } as never).appearance.zoom).toBe(0.7);
    expect(migrate({ appearance: { theme: "blue", zoom: 1 } } as never).appearance.theme).toBe(
      "system",
    );
  });

  it("旧 engine 字段 → mode/ruleEngine；旧 textModel → postModel", () => {
    const a = migrate({ defaults: { engine: "vlm" } } as never);
    expect(a.defaults.mode).toBe("vlm");
    const b = migrate({ defaults: { engine: "paddleocr" }, textModel: "deepseek/x" } as never);
    expect(b.defaults.mode).toBe("rule");
    expect(b.defaults.ruleEngine).toBe("paddleocr");
    expect(b.defaults.postModel).toBe("deepseek/x");
  });

  it("空输入 → 全默认（含 zoom 1、强制 OCR 默认开）", () => {
    const s = migrate({});
    expect(s.appearance).toEqual({ theme: "system", zoom: 1 });
    expect(s.defaults.ocr).toBe(true);
  });
});
