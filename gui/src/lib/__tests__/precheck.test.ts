import { describe, expect, it } from "vitest";
import { precheckTask } from "../precheck";
import { defaultSettings, type Settings } from "../settings";

function withProviders(s: Settings): Settings {
  s.providers = [
    { id: "zai", baseUrl: "https://api.z.ai", apiKey: "k-zai", enabled: true, models: [] },
    { id: "nokey", baseUrl: "https://x", apiKey: "", enabled: true, models: [] },
  ];
  return s;
}

describe("precheckTask 缺 key 预检", () => {
  it("规则+MinerU：缺 mineru token → 拦，指去解析模型分组", () => {
    const s = defaultSettings();
    const o = s.defaults; // 默认 rule+mineru
    const r = precheckTask(o, s);
    expect(r).toMatchObject({ ok: false, missing: "MinerU Token", section: "parse" });
  });

  it("规则+MinerU：有 token → 放行", () => {
    const s = defaultSettings();
    s.ocr.mineruToken = "tok";
    expect(precheckTask(s.defaults, s).ok).toBe(true);
  });

  it("规则+PaddleOCR：缺 paddle token → 拦", () => {
    const s = defaultSettings();
    s.defaults.ruleEngine = "paddleocr";
    const r = precheckTask(s.defaults, s);
    expect(r).toMatchObject({ ok: false, missing: "PaddleOCR Token", section: "parse" });
  });

  it("规则：选了后处理模型但其提供商 key 为空 → 拦并带 providerId", () => {
    const s = withProviders(defaultSettings());
    s.ocr.mineruToken = "tok";
    s.defaults.postModel = "nokey/deepseek-chat";
    const r = precheckTask(s.defaults, s);
    expect(r).toMatchObject({ ok: false, section: "providers", providerId: "nokey" });
  });

  it("规则：后处理模型未选 → 不查（.env 兜底）", () => {
    const s = defaultSettings();
    s.ocr.mineruToken = "tok";
    expect(precheckTask(s.defaults, s).ok).toBe(true);
  });

  it("VLM：未选多模态模型 → 拦", () => {
    const s = defaultSettings();
    s.defaults.mode = "vlm";
    const r = precheckTask(s.defaults, s);
    expect(r.ok).toBe(false);
  });

  it("VLM：所选模型提供商有 key → 放行", () => {
    const s = withProviders(defaultSettings());
    s.defaults.mode = "vlm";
    s.defaults.vlmModel = "zai/glm-5.3-flash";
    expect(precheckTask(s.defaults, s).ok).toBe(true);
  });

  it("VLM：所选模型提供商缺 key → 拦", () => {
    const s = withProviders(defaultSettings());
    s.defaults.mode = "vlm";
    s.defaults.vlmModel = "nokey/glm-4.6v";
    const r = precheckTask(s.defaults, s);
    expect(r).toMatchObject({ ok: false, section: "providers", providerId: "nokey" });
  });
});
