import { describe, expect, it } from "vitest";
import {
  buildCliArgs,
  buildEnv,
  defaultSettings,
  engineOf,
} from "../settings";

function base() {
  const s = defaultSettings();
  s.ocr.mineruToken = "mineru-tok";
  s.ocr.paddleocrToken = "paddle-tok";
  s.providers = [
    {
      id: "zai",
      baseUrl: "https://api.z.ai/api/paas/v4",
      apiKey: "zai-key",
      enabled: true,
      models: [],
    },
    {
      id: "deepseek",
      baseUrl: "https://api.deepseek.com",
      apiKey: "ds-key",
      enabled: true,
      models: [],
    },
  ];
  return s;
}

describe("buildEnv VLM 同源注入", () => {
  it("VLM 模式：所选模型的提供商三件套同名注入 VLM_* 与 DEEPSEEK_*", () => {
    const s = base();
    const o = { ...s.defaults, mode: "vlm" as const, vlmModel: "zai/glm-5.3-flash" };
    const env = buildEnv(s, o);
    expect(env.VLM_API_KEY).toBe("zai-key");
    expect(env.VLM_BASE_URL).toBe("https://api.z.ai/api/paas/v4");
    expect(env.VLM_MODEL).toBe("glm-5.3-flash");
    // 同源：Stage 2/4 走同一模型
    expect(env.DEEPSEEK_API_KEY).toBe("zai-key");
    expect(env.DEEPSEEK_BASE_URL).toBe("https://api.z.ai/api/paas/v4");
    expect(env.DEEPSEEK_MODEL).toBe("glm-5.3-flash");
    expect(env.OCR_PROVIDER).toBe("vlm");
  });

  it("规则模式：DEEPSEEK_* 来自后处理模型所属提供商，不注入 VLM_*", () => {
    const s = base();
    const o = { ...s.defaults, mode: "rule" as const, postModel: "deepseek/deepseek-chat" };
    const env = buildEnv(s, o);
    expect(env.DEEPSEEK_API_KEY).toBe("ds-key");
    expect(env.DEEPSEEK_MODEL).toBe("deepseek-chat");
    expect(env.VLM_API_KEY).toBeUndefined();
    expect(env.OCR_PROVIDER).toBe("mineru");
  });

  it("未选模型：不注入对应三件套（.env 兜底）", () => {
    const s = base();
    const env = buildEnv(s, { ...s.defaults, mode: "vlm" as const, vlmModel: "" });
    expect(env.VLM_API_KEY).toBeUndefined();
    expect(env.DEEPSEEK_API_KEY).toBeUndefined();
  });

  it("提示音单开关：关闭 → 完成/失败双 env 同 off；开启都不注入", () => {
    const s = base();
    s.sound = false;
    const envOff = buildEnv(s, s.defaults);
    expect(envOff.CONVERT_COMPLETE_SOUND).toBe("off");
    expect(envOff.CONVERT_FAIL_SOUND).toBe("off");
    s.sound = true;
    const envOn = buildEnv(s, s.defaults);
    expect(envOn.CONVERT_COMPLETE_SOUND).toBeUndefined();
    expect(envOn.CONVERT_FAIL_SOUND).toBeUndefined();
  });
});

describe("buildCliArgs / engineOf", () => {
  it("VLM 模式 engine=vlm 且无 --no-ocr（VLM 无 OCR 概念）", () => {
    const s = base();
    const o = { ...s.defaults, mode: "vlm" as const, ocr: false, outputDir: "F:/out" };
    expect(engineOf(o)).toBe("vlm");
    const args = buildCliArgs(o);
    expect(args).toContain("vlm");
    expect(args).not.toContain("--no-ocr");
  });

  it("规则模式关 OCR → --no-ocr", () => {
    const s = base();
    const o = { ...s.defaults, ocr: false, outputDir: "F:/out" };
    expect(buildCliArgs(o)).toContain("--no-ocr");
  });

  it("翻译与格式子选项", () => {
    const s = base();
    const o = {
      ...s.defaults,
      outputDir: "F:/out",
      formats: ["epub", "md", "tex"],
      mdSplit: true,
      mdDialect: "pandoc" as const,
      texFull: false,
      exportLang: "both" as const,
      translate: true,
      translateLang: "en",
    };
    const args = buildCliArgs(o).join(" ");
    expect(args).toContain("--format epub,md,tex");
    expect(args).toContain("--md-split");
    expect(args).toContain("--md-dialect pandoc");
    expect(args).toContain("--tex-fragment");
    expect(args).toContain("--export-lang both");
    expect(args).toContain("--translate en");
  });

  it("导出语言跟翻译开关走、与格式无关（061/062）", () => {
    // 061：原先嵌在 tex 分支，只选 md 时被静默丢弃；
    // 062：EPUB 也吃 export_lang → 不再按格式门控，开翻译即下发
    const s = base();
    const mdOnly = {
      ...s.defaults,
      outputDir: "F:/out",
      formats: ["epub", "md"],
      translate: true,
      exportLang: "orig" as const,
    };
    expect(buildCliArgs(mdOnly).join(" ")).toContain("--export-lang orig");
    const epubOnly = { ...mdOnly, formats: ["epub"], exportLang: "both" as const };
    expect(buildCliArgs(epubOnly).join(" ")).toContain("--export-lang both");
    // 没开翻译：无译文可导，不下发（auto 是默认值也不下发）
    const noTrans = { ...mdOnly, translate: false };
    expect(buildCliArgs(noTrans).join(" ")).not.toContain("--export-lang");
  });
});
