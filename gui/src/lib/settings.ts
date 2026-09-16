// 设置 schema（v2，与旧 tkinter gui_settings.json 分离）+ 模块级 store
import { invoke } from "@tauri-apps/api/core";
import { useSyncExternalStore } from "react";
import { presetOf } from "./providers";

export interface ActivatedModel {
  id: string;
  vision: boolean;
  enabled: boolean;
}

export interface ProviderConfig {
  id: string; // 对齐 PROVIDER_PRESETS.id
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
  models: ActivatedModel[];
}

export interface ConvertOptions {
  mode: "rule" | "vlm";
  ruleEngine: "mineru" | "paddleocr";
  ocr: boolean; // 仅规则模式有意义
  translate: boolean;
  translateLang: string;
  outputDir: string;
  formats: string[]; // epub/md/tex 子集
  mdSplit: boolean;
  mdDialect: "gfm" | "pandoc";
  texFull: boolean;
  exportLang: "auto" | "orig" | "trans" | "both";
  vlmModel: string; // 多模态模型 "providerId/modelId"，空=回退 .env
  postModel: string; // 后处理模型 "providerId/modelId"（规则模式），空=回退 .env
  reasoning: "off" | "low" | "medium" | "high";
  workers: number; // 1-8
}

export interface Settings {
  version: 2;
  ocr: {
    mineruToken: string;
    paddleocrToken: string;
    paddleocrApiUrl: string;
  };
  providers: ProviderConfig[];
  defaults: ConvertOptions;
  appearance: {
    theme: "light" | "dark" | "system";
    zoom: number; // 0.7–1.5，启动时恢复
  };
  sound: boolean; // 完成提示音
  scanDirs: string[];
  historyDirs: string[];
}

export const DEFAULT_PADDLEOCR_API_URL =
  "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs";

export function defaultConvertOptions(): ConvertOptions {
  return {
    mode: "rule",
    ruleEngine: "mineru",
    ocr: true,
    translate: false,
    translateLang: "zh",
    outputDir: "",
    formats: ["epub"],
    mdSplit: false,
    mdDialect: "gfm",
    texFull: true,
    exportLang: "auto",
    vlmModel: "",
    postModel: "",
    reasoning: "low",
    workers: 4,
  };
}

export function defaultSettings(): Settings {
  return {
    version: 2,
    ocr: { mineruToken: "", paddleocrToken: "", paddleocrApiUrl: DEFAULT_PADDLEOCR_API_URL },
    providers: [],
    defaults: defaultConvertOptions(),
    appearance: { theme: "system", zoom: 1 },
    sound: true,
    scanDirs: [],
    historyDirs: [],
  };
}

/** 选项 → pipeline --engine 值 */
export function engineOf(o: ConvertOptions): "mineru" | "paddleocr" | "vlm" {
  return o.mode === "vlm" ? "vlm" : o.ruleEngine;
}

/** "providerId/modelId" 解析；providerId 本身不含 / */
export function splitModelRef(ref: string): { providerId: string; modelId: string } | null {
  const i = ref.indexOf("/");
  if (i <= 0) return null;
  return { providerId: ref.slice(0, i), modelId: ref.slice(i + 1) };
}

export function findProvider(s: Settings, id: string): ProviderConfig | undefined {
  return s.providers.find((p) => p.id === id);
}

/** 已启用（有 key）提供商 × 已启用型号；visionOnly=true 时只留视觉型号 */
export function activatedModels(s: Settings, visionOnly: boolean) {
  const out: { ref: string; providerId: string; modelId: string; vision: boolean }[] = [];
  for (const p of s.providers) {
    if (p.enabled === false) continue;
    if (!p.apiKey.trim()) continue; // 无 key 的提供商等价禁用（与设置页 toggle 逻辑一致）
    for (const m of p.models) {
      if (m.enabled === false) continue;
      if (visionOnly && !m.vision) continue;
      out.push({ ref: `${p.id}/${m.id}`, providerId: p.id, modelId: m.id, vision: m.vision });
    }
  }
  return out;
}

// ── sidecar 环境变量/CLI 参数装配（空值不注入，.env 兜底） ──

/** 思考档可见性守卫：仅支持思考参数的端点才暴露该控件（vlm_client.py 对未收录端点不下发思考参数）。
 *  未选模型（.env 兜底）→ true（显示，保持默认行为）；
 *  预设提供商查 supportsReasoning 标记；孤儿/自定义按 baseUrl host 对齐 vlm_client.py 的分派口径。 */
export function reasoningSupported(s: Settings, modelRef: string): boolean {
  const ref = splitModelRef(modelRef);
  if (!ref) return true;
  const preset = presetOf(ref.providerId);
  if (preset) return preset.supportsReasoning === true;
  const host = (findProvider(s, ref.providerId)?.baseUrl ?? "").toLowerCase();
  return ["z.ai", "bigmodel", "deepseek", "dashscope"].some((h) => host.includes(h));
}

export function buildEnv(s: Settings, o: ConvertOptions): Record<string, string> {
  const env: Record<string, string> = {
    OCR_PROVIDER: engineOf(o),
    MINERU_TOKEN: s.ocr.mineruToken,
    PADDLEOCR_TOKEN: s.ocr.paddleocrToken,
    PADDLEOCR_API_URL: s.ocr.paddleocrApiUrl,
  };
  if (!s.sound) env.CONVERT_COMPLETE_SOUND = "off";

  if (o.mode === "vlm") {
    env.VLM_REASONING = o.reasoning;
    env.VLM_WORKERS = String(o.workers);
    const vlm = splitModelRef(o.vlmModel);
    const p = vlm ? findProvider(s, vlm.providerId) : undefined;
    if (vlm && p) {
      env.VLM_API_KEY = p.apiKey;
      env.VLM_BASE_URL = p.baseUrl;
      env.VLM_MODEL = vlm.modelId;
      // 用户裁定「VLM 后处理也同样用 VLM」：同源注入 Stage 2/4 三件套
      env.DEEPSEEK_API_KEY = p.apiKey;
      env.DEEPSEEK_BASE_URL = p.baseUrl;
      env.DEEPSEEK_MODEL = vlm.modelId;
    }
  } else {
    const post = splitModelRef(o.postModel);
    const p = post ? findProvider(s, post.providerId) : undefined;
    if (post && p) {
      env.DEEPSEEK_API_KEY = p.apiKey;
      env.DEEPSEEK_BASE_URL = p.baseUrl;
      env.DEEPSEEK_MODEL = post.modelId;
    }
  }
  return env;
}

export function buildCliArgs(o: ConvertOptions): string[] {
  const args: string[] = ["--engine", engineOf(o), "-o", o.outputDir];
  if (o.mode === "rule" && !o.ocr) args.push("--no-ocr"); // VLM 无 OCR 概念
  args.push("--format", o.formats.join(","));
  if (o.formats.includes("md")) {
    if (o.mdSplit) args.push("--md-split");
    if (o.mdDialect !== "gfm") args.push("--md-dialect", o.mdDialect);
  }
  if (o.formats.includes("tex")) {
    if (!o.texFull) args.push("--tex-fragment");
    if (o.exportLang !== "auto") args.push("--export-lang", o.exportLang);
  }
  if (o.translate) args.push("--translate", o.translateLang || "zh");
  return args;
}

// ── 模块级 store：App 启动时 load 一次，之后全内存读写 + 落盘 ──

type Listener = () => void;

/** 旧字段迁移：engine → mode/ruleEngine；textModel → defaults.postModel；
 *  provider/model 补 enabled；appearance 字符串 → {theme, zoom}（导出供 vitest） */
export function migrate(parsed: Partial<Settings> & Record<string, unknown>): Settings {
  const base = defaultSettings();
  const d: ConvertOptions & { engine?: string } = {
    ...base.defaults,
    ...((parsed.defaults as object) ?? {}),
  };
  if (d.engine !== undefined) {
    if (d.engine === "vlm") d.mode = "vlm";
    else {
      d.mode = "rule";
      d.ruleEngine = d.engine === "paddleocr" ? "paddleocr" : "mineru";
    }
    delete d.engine;
  }
  if (!d.postModel && typeof parsed.textModel === "string") {
    d.postModel = parsed.textModel;
  }
  const rawProviders = (parsed.providers ?? []) as Partial<ProviderConfig>[];
  const providers: ProviderConfig[] = rawProviders.map((p) => ({
    id: p.id ?? "",
    baseUrl: p.baseUrl ?? "",
    apiKey: p.apiKey ?? "",
    enabled: p.enabled ?? true,
    models: (p.models ?? []).map((m) => ({
      id: m.id ?? "",
      vision: m.vision ?? true,
      enabled: m.enabled ?? true,
    })),
  }));
  // appearance：旧版是纯字符串主题；zoom 收敛到 0.7–1.5
  let appearance = base.appearance;
  const rawAppearance = parsed.appearance as unknown;
  if (typeof rawAppearance === "string") {
    appearance = {
      theme: (["light", "dark", "system"].includes(rawAppearance)
        ? rawAppearance
        : "system") as Settings["appearance"]["theme"],
      zoom: 1,
    };
  } else if (rawAppearance && typeof rawAppearance === "object") {
    const a = rawAppearance as Partial<Settings["appearance"]>;
    appearance = {
      theme: (["light", "dark", "system"].includes(a.theme ?? "")
        ? a.theme
        : "system") as Settings["appearance"]["theme"],
      zoom: Math.min(1.5, Math.max(0.7, Number(a.zoom) || 1)),
    };
  }
  return {
    ...base,
    ...parsed,
    ocr: { ...base.ocr, ...(parsed.ocr ?? {}) },
    providers,
    defaults: d,
    appearance,
    sound: parsed.sound ?? true,
  };
}

class SettingsStore {
  settings: Settings = defaultSettings();
  loaded = false;
  private listeners = new Set<Listener>();

  async load() {
    try {
      const raw = await invoke<string | null>("load_settings");
      if (raw) {
        this.settings = migrate(JSON.parse(raw));
      } else {
        // 首次运行：预填默认输出目录建议值
        const dir = await invoke<string>("default_output_dir").catch(() => "");
        if (dir) this.settings.defaults.outputDir = dir;
      }
    } catch (e) {
      console.error("load_settings failed", e);
    }
    this.loaded = true;
    this.emit();
  }

  update(patch: Partial<Settings>) {
    this.settings = { ...this.settings, ...patch };
    this.emit();
    invoke("save_settings", { json: JSON.stringify(this.settings, null, 2) }).catch((e) =>
      console.error("save_settings failed", e),
    );
  }

  addHistoryDir(dir: string) {
    if (!dir) return;
    const rest = this.settings.historyDirs.filter((d) => d !== dir);
    this.update({ historyDirs: [dir, ...rest].slice(0, 20) });
  }

  subscribe = (l: Listener) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
  getVersion = () => this.settings;
  private emit() {
    this.listeners.forEach((l) => l());
  }
}

export const settingsStore = new SettingsStore();

export function useSettings(): Settings {
  return useSyncExternalStore(settingsStore.subscribe, settingsStore.getVersion);
}
