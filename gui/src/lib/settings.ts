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
  // 提示音单开关（病例 059 用户裁定：开=完成+失败都响，关=都静音；
  // CLI 侧的 CONVERT_*_SOUND 独立 env 语义不动，那是 CLI 的事）
  if (!s.sound) {
    env.CONVERT_COMPLETE_SOUND = "off";
    env.CONVERT_FAIL_SOUND = "off";
  }

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

/** 仓库内暂存区判定（病例 052）：dev 冒烟产物目录（_regress/、output/、.tmp-*）
 *  不进 historyDirs。以路径中的 books_converter 段锚定，判不准时宁可 false
 *  （只可能少清理，绝不误伤用户正常目录——铁律 0 的失败方向）。 */
export function isRepoInternalDir(dir: string): boolean {
  const norm = dir.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  const m = norm.match(/(?:^|\/)books_converter\/(.+)$/);
  if (!m) return false;
  const rest = m[1];
  return ["_regress", "output", ".tmp-epub-zh", ".tmp-toc-fix"].some(
    (seg) => rest === seg || rest.startsWith(seg + "/"),
  );
}

/** historyDirs 清理的纯函数部分：剔除仓库内暂存区（存在性剔除走 Rust
 *  existing_dirs，异步侧见 SettingsStore.pruneHistoryDirs）（病例 052） */
export function pruneRepoInternalDirs(dirs: string[]): string[] {
  return dirs.filter((d) => d && !isRepoInternalDir(d));
}

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
  // 病例 053：扫描/忽略功能移除——旧配置里已持久化的 scanDirs/ignoredPaths
  // 静默丢弃（加载不炸、下次保存落盘即清），不加校验也不报错
  const merged: Settings = {
    ...base,
    ...parsed,
    ocr: { ...base.ocr, ...(parsed.ocr ?? {}) },
    providers,
    defaults: d,
    appearance,
    sound: parsed.sound ?? true,
  };
  const dropped = merged as unknown as Record<string, unknown>;
  delete dropped.scanDirs;
  delete dropped.ignoredPaths;
  return merged;
}

/** read_settings_state 命令的返回形状 */
interface SettingsFileState {
  mtime_ms: number | null;
  content: string | null;
}

class SettingsStore {
  settings: Settings = defaultSettings();
  loaded = false;
  private listeners = new Set<Listener>();
  /** 最后一次亲自读到/写到的文件内容原文（聚焦重载的变更判定基准） */
  private lastSyncedJson: string | null = null;
  /** 最后一次亲自见到的文件 mtime（毫秒） */
  private fileMtimeMs: number | null = null;
  /** 最后一次亲自发起保存的墙钟时间（自己写入引起的 mtime 变化不触发重载） */
  private lastWriteAt = 0;
  private reloading = false;

  async load() {
    try {
      // 首选带 mtime 的状态读（dev 分叉后读 settings.dev.json）；
      // 命令不可用/读失败 → 退回 load_settings（失败方向=保住加载链路）
      let raw: string | null = null;
      try {
        const st = await invoke<SettingsFileState>("read_settings_state");
        if (st.content) {
          this.fileMtimeMs = st.mtime_ms;
          raw = st.content;
        }
      } catch {
        /* fallthrough 到 load_settings */
      }
      if (!raw) {
        // dev 文件尚不存在：load_settings 在 debug 下一次性继承正式配置
        raw = await invoke<string | null>("load_settings");
      }
      if (raw) {
        this.settings = migrate(JSON.parse(raw));
        this.lastSyncedJson = raw;
      } else {
        // 首次运行：预填默认输出目录建议值
        const dir = await invoke<string>("default_output_dir").catch(() => "");
        if (dir) this.settings.defaults.outputDir = dir;
      }
    } catch (e) {
      console.error("load_settings failed", e);
    }
    await this.pruneHistoryDirs();
    this.loaded = true;
    this.emit();
  }

  /** 窗口聚焦重载（病例 052，多实例内存分叉的对策）：磁盘内容比内存新才替换；
   *  读取/解析失败、文件被删、内容与自己同步过的一致 → 全部保持内存不动作（铁律 0） */
  async reloadIfChanged() {
    if (!this.loaded || this.reloading) return;
    this.reloading = true;
    try {
      const st = await invoke<SettingsFileState>("read_settings_state");
      if (st.content == null) return; // 文件不存在（dev 还没保存过）→ 不动作
      if (st.mtime_ms != null) {
        if (this.fileMtimeMs != null && st.mtime_ms === this.fileMtimeMs) return; // 同一版本
        if (this.lastWriteAt > 0 && st.mtime_ms <= this.lastWriteAt) return; // 自己刚写的
      }
      if (st.content === this.lastSyncedJson) {
        this.fileMtimeMs = st.mtime_ms;
        return; // 内容一致（可能是自己保存后 mtime 更新）
      }
      const next = migrate(JSON.parse(st.content)); // 解析失败抛错 → catch 保持内存
      this.settings = next;
      this.lastSyncedJson = st.content;
      this.fileMtimeMs = st.mtime_ms;
      await this.pruneHistoryDirs();
      this.emit();
    } catch (e) {
      console.warn("settings 聚焦重载跳过（保持内存）", e);
    } finally {
      this.reloading = false;
    }
  }

  /** 病例 052 去污：historyDirs 剔除仓库内暂存区与已不存在的目录（内存生效，
   *  下次保存落盘；存在性检查失败=保留原样，不动作） */
  private async pruneHistoryDirs() {
    const before = this.settings.historyDirs;
    let kept = pruneRepoInternalDirs(before);
    try {
      kept = await invoke<string[]>("existing_dirs", { dirs: kept });
    } catch {
      /* 存在性判定不可用 → 只做仓库内剔除，其余保留 */
    }
    const changed =
      kept.length !== before.length || kept.some((d, i) => d !== before[i]);
    if (changed) {
      this.settings = { ...this.settings, historyDirs: kept };
    }
  }

  update(patch: Partial<Settings>) {
    this.settings = { ...this.settings, ...patch };
    this.emit();
    const json = JSON.stringify(this.settings, null, 2);
    this.lastSyncedJson = json;
    this.lastWriteAt = Date.now();
    invoke("save_settings", { json }).catch((e) =>
      console.error("save_settings failed", e),
    );
  }

  addHistoryDir(dir: string) {
    if (!dir) return;
    // 病例 052：dev 冒烟的仓库内暂存目录不再持久化进历史（污染源之一）
    if (isRepoInternalDir(dir)) return;
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
