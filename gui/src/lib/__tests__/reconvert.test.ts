import { describe, expect, it } from "vitest";
import { buildReconvertOptions } from "../reconvert";
import { defaultSettings } from "../settings";

const REG_DIR = "F:/out/gui-smoke3";

function settingsWithVlm() {
  const s = defaultSettings();
  s.providers = [
    {
      id: "zai",
      baseUrl: "https://api.z.ai/api/paas/v4",
      apiKey: "k",
      enabled: true,
      models: [{ id: "glm-5.3-flash", vision: true, enabled: true }],
    },
  ];
  return s;
}

describe("buildReconvertOptions 再次转换选项重组装", () => {
  it("VLM 记录：原模型仍在激活列表 → 预选模型+思考档", () => {
    const s = settingsWithVlm();
    const { options, modelFallback } = buildReconvertOptions(
      {
        engine: "vlm",
        ocr: true,
        translate: "zh",
        formats: ["epub", "md"],
        vlm_model: "glm-5.3-flash",
        vlm_reasoning: "medium",
        registryDir: REG_DIR,
      },
      s,
    );
    expect(modelFallback).toBe(false);
    expect(options.mode).toBe("vlm");
    expect(options.vlmModel).toBe("zai/glm-5.3-flash");
    expect(options.reasoning).toBe("medium");
    expect(options.translate).toBe(true);
    expect(options.translateLang).toBe("zh");
    expect(options.formats).toEqual(["epub", "md"]);
    expect(options.outputDir).toBe(REG_DIR);
  });

  it("VLM 记录：原模型已不在激活列表 → 回退默认 + fallback 标记", () => {
    const s = settingsWithVlm();
    const { options, modelFallback } = buildReconvertOptions(
      {
        engine: "vlm",
        ocr: true,
        translate: null,
        formats: ["epub"],
        vlm_model: "glm-4.6v", // 已移除的旧型号
        vlm_reasoning: "high",
        registryDir: REG_DIR,
      },
      s,
    );
    expect(modelFallback).toBe(true);
    expect(options.vlmModel).toBe(s.defaults.vlmModel); // 回退当前默认
    expect(options.reasoning).toBe(s.defaults.reasoning);
  });

  it("老记录（无 vlm_model 字段）→ 按 undefined 回退，不误报 fallback", () => {
    const s = settingsWithVlm();
    const { options, modelFallback } = buildReconvertOptions(
      { engine: "vlm", ocr: true, translate: null, formats: ["epub"], registryDir: REG_DIR },
      s,
    );
    expect(modelFallback).toBe(false);
    expect(options.mode).toBe("vlm");
  });

  it("规则记录：engine 映射 + ocr 回读 + registry 未登记的字段回退默认", () => {
    const s = defaultSettings();
    s.defaults.mdSplit = true; // 当前设置页默认
    const { options } = buildReconvertOptions(
      {
        engine: "paddleocr",
        ocr: false,
        translate: null,
        formats: ["epub", "tex"],
        registryDir: REG_DIR,
      },
      s,
    );
    expect(options.mode).toBe("rule");
    expect(options.ruleEngine).toBe("paddleocr");
    expect(options.ocr).toBe(false);
    expect(options.translate).toBe(false);
    expect(options.mdSplit).toBe(true); // registry v1 没有，回退当前默认
    expect(options.formats).toEqual(["epub", "tex"]);
  });

  it("formats 全非法 → 兜底 epub；未知 engine → mineru", () => {
    const s = defaultSettings();
    const { options } = buildReconvertOptions(
      { engine: "hybrid", ocr: true, translate: null, formats: ["pdf"], registryDir: REG_DIR },
      s,
    );
    expect(options.formats).toEqual(["epub"]);
    expect(options.ruleEngine).toBe("mineru");
  });
});
