import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import {
  ArrowLeft,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  CloudDownload,
  ExternalLink,
  Eye,
  FolderOpen,
  Monitor,
  Moon,
  Play,
  Plus,
  RefreshCw,
  ScanText,
  Settings2,
  Sun,
  X,
} from "lucide-react";
import {
  activatedModels,
  findProvider,
  settingsStore,
  useSettings,
  type ConvertOptions,
  type ProviderConfig,
} from "../lib/settings";
import { presetOf, PROVIDER_PRESETS, presetName } from "../lib/providers";
import { modelSupportsVision } from "../lib/vision-map";
import { notify } from "../lib/notify";
import type { SettingsSection } from "../lib/precheck";
import { S } from "../lib/strings";
import { Badge } from "../components/Badge";
import { ProviderIcon } from "../components/ProviderIcon";
import { Select } from "../components/Select";
import { Toggle } from "../components/Toggle";
import { Tooltip } from "../components/Tooltip";

const APP_VERSION = "1.3.9"; // 与仓库 version.py 对齐

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card mt-4 p-4">
      <div className="mb-2 text-[14px] font-semibold" style={{ color: "var(--ink2)" }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Row({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0">
        <div className="text-[14px] font-medium">{label}</div>
        {desc && (
          <div className="mt-0.5 text-xs" style={{ color: "var(--ink2)" }}>
            {desc}
          </div>
        )}
      </div>
      <div className="w-80 shrink-0">{children}</div>
    </div>
  );
}

function SignupLink({ url }: { url?: string }) {
  if (!url) return null;
  return (
    <button
      className="btn btn-ghost cursor-pointer text-xs"
      style={{ color: "var(--accent)" }}
      onClick={() => invoke("open_url", { url }).catch(() => {})}
    >
      <ExternalLink size={12} /> {S.settings.signup}
    </button>
  );
}

/** 提供商有效启用态：无 key 恒禁用（SageRead 同款逻辑） */
function effectiveEnabled(cfg: ProviderConfig | undefined): boolean {
  return !!cfg && cfg.enabled !== false && !!cfg.apiKey.trim();
}

// ── ① 解析模型 ──

function ParseSection() {
  const settings = useSettings();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const setOcr = (patch: Partial<typeof settings.ocr>) =>
    settingsStore.update({ ocr: { ...settings.ocr, ...patch } });

  return (
    <>
      <Group title={`${S.settings.secParse} · MinerU`}>
        <div className="mb-1 flex items-center justify-between">
          <div className="text-xs" style={{ color: "var(--ink2)" }}>
            {S.settings.mineruDesc}
          </div>
          <SignupLink url="https://mineru.net/" />
        </div>
        <Row label={S.settings.mineruToken}>
          <input
            className="input mono"
            type="password"
            placeholder="sk-…"
            value={settings.ocr.mineruToken}
            onChange={(e) => setOcr({ mineruToken: e.target.value })}
          />
        </Row>
      </Group>
      <Group title={`${S.settings.secParse} · PaddleOCR`}>
        <div className="mb-1 flex items-center justify-between">
          <div className="text-xs" style={{ color: "var(--ink2)" }}>
            {S.settings.paddleocrDesc}
          </div>
          <SignupLink url="https://aistudio.baidu.com/" />
        </div>
        <Row label={S.settings.paddleocrToken}>
          <input
            className="input mono"
            type="password"
            placeholder="token"
            value={settings.ocr.paddleocrToken}
            onChange={(e) => setOcr({ paddleocrToken: e.target.value })}
          />
        </Row>
        <button
          className="btn btn-ghost mt-1 text-xs"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {S.settings.advanced}
        </button>
        {showAdvanced && (
          <Row label={S.settings.paddleocrApiUrl}>
            <input
              className="input mono"
              value={settings.ocr.paddleocrApiUrl}
              onChange={(e) => setOcr({ paddleocrApiUrl: e.target.value })}
            />
          </Row>
        )}
      </Group>
    </>
  );
}

// ── ② 大模型提供商 ──

function ProviderDetail({
  presetId,
  onBack,
}: {
  presetId: string;
  onBack: () => void;
}) {
  const settings = useSettings();
  const [testing, setTesting] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [modelInput, setModelInput] = useState("");

  const cfg: ProviderConfig = findProvider(settings, presetId) ?? {
    id: presetId,
    baseUrl: presetOf(presetId)?.baseUrl ?? "",
    apiKey: "",
    enabled: true,
    models: [],
  };

  const save = (patch: Partial<ProviderConfig>) => {
    const rest = settings.providers.filter((p) => p.id !== presetId);
    const next = { ...cfg, ...patch };
    // 填入 key 即允许启用（自动点亮；清空 key 自动禁用）
    if (patch.apiKey !== undefined) next.enabled = !!patch.apiKey.trim();
    settingsStore.update({ providers: [...rest, next] });
  };

  const test = async () => {
    setTesting(true);
    try {
      const msg = await invoke<string>("test_connection", {
        baseUrl: cfg.baseUrl,
        apiKey: cfg.apiKey,
      });
      notify.success(msg);
    } catch (e) {
      notify.error(S.settings.toastTestFail(String(e)));
    } finally {
      setTesting(false);
    }
  };

  const addModel = (id: string) => {
    const mid = id.trim();
    if (!mid || cfg.models.some((m) => m.id === mid)) return;
    const vision = modelSupportsVision(presetId, mid);
    save({ models: [...cfg.models, { id: mid, vision, enabled: true }] });
  };

  const activate = () => {
    addModel(modelInput);
    setModelInput("");
  };

  const fetchAll = async () => {
    setFetching(true);
    try {
      const ids = await invoke<string[]>("fetch_models", {
        baseUrl: cfg.baseUrl,
        apiKey: cfg.apiKey,
      });
      const existing = new Set(cfg.models.map((m) => m.id));
      const fresh = ids.filter((i) => !existing.has(i));
      fresh.forEach(addModel);
      if (fresh.length) notify.success(S.settings.toastFetched(fresh.length));
      else notify.info(S.settings.toastFetchedNone);
    } catch (e) {
      notify.error(S.settings.toastTestFail(String(e)));
    } finally {
      setFetching(false);
    }
  };

  return (
    <div>
      <button className="btn btn-ghost mb-3" onClick={onBack}>
        <ArrowLeft size={14} /> {S.settings.backToProviders}
      </button>
      <div className="card p-4">
        <div className="mb-3 flex items-center gap-2 text-[15px] font-semibold">
          <ProviderIcon id={presetId} size={17} style={{ color: "var(--accent)" }} />
          {presetName(presetId)}
          <span className="flex-1" />
          <SignupLink url={presetOf(presetId)?.signupUrl} />
        </div>

        <Row label={S.settings.apiKey} desc={cfg.apiKey ? S.settings.apiKeySaved : undefined}>
          <div className="flex items-center gap-2">
            <input
              className="input mono"
              type="password"
              placeholder={S.settings.apiKeyPlaceholder}
              value={cfg.apiKey}
              onChange={(e) => save({ apiKey: e.target.value })}
            />
            {cfg.apiKey && (
              <Tooltip label={S.settings.apiKeyClear}>
                <button className="icon-btn shrink-0" onClick={() => save({ apiKey: "" })}>
                  <X size={14} />
                </button>
              </Tooltip>
            )}
          </div>
        </Row>
        <Row label={S.settings.baseUrl}>
          <input
            className="input mono"
            placeholder="https://…"
            value={cfg.baseUrl}
            onChange={(e) => save({ baseUrl: e.target.value })}
          />
        </Row>
        <div className="py-2">
          <button className="btn" disabled={testing || !cfg.baseUrl} onClick={test}>
            {testing ? S.settings.testing : S.settings.testConnection}
          </button>
        </div>

        <div className="mt-3 mb-2 flex items-center justify-between">
          <div className="text-[14px] font-semibold">{S.settings.modelList}</div>
          <button className="btn" disabled={fetching || !cfg.baseUrl} onClick={fetchAll}>
            <CloudDownload size={14} />
            {fetching ? S.settings.fetching : S.settings.fetchModels}
          </button>
        </div>
        <div className="mb-2 flex items-center gap-2">
          <input
            className="input mono"
            value={modelInput}
            placeholder={S.settings.addModel}
            onChange={(e) => setModelInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && activate()}
          />
          <Tooltip label={S.settings.add}>
            <button className="icon-btn shrink-0" disabled={!modelInput.trim()} onClick={activate}>
              <Plus size={15} />
            </button>
          </Tooltip>
        </div>
        <div className="flex flex-col gap-1.5">
          {cfg.models.map((m) => (
            <div key={m.id} className="card flex items-center gap-2 px-2.5 py-1.5">
              <Toggle
                checked={m.enabled !== false}
                onChange={(v) =>
                  save({
                    models: cfg.models.map((x) => (x.id === m.id ? { ...x, enabled: v } : x)),
                  })
                }
              />
              <span className="mono flex-1 truncate text-[14px]">{m.id}</span>
              {m.vision ? (
                <Badge tone="accent">
                  <Eye size={10} /> {S.settings.visionYes}
                </Badge>
              ) : (
                <Badge tone="muted">{S.settings.visionNo}</Badge>
              )}
              <Tooltip label={S.settings.removeModel}>
                <button
                  className="icon-btn"
                  style={{ width: 22, height: 22 }}
                  onClick={() => save({ models: cfg.models.filter((x) => x.id !== m.id) })}
                >
                  <X size={12} />
                </button>
              </Tooltip>
            </div>
          ))}
          {!cfg.models.length && (
            <div className="py-3 text-center text-xs" style={{ color: "var(--ink2)" }}>
              {S.settings.addModel}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ProvidersSection({ detailId, setDetailId }: { detailId: string | null; setDetailId: (id: string | null) => void }) {
  const settings = useSettings();

  if (detailId) {
    return <ProviderDetail presetId={detailId} onBack={() => setDetailId(null)} />;
  }

  // 预设列表 + 历史配置里已存在但不在预设中的提供商（孤儿配置不丢，排末尾）
  const presetIds = new Set(PROVIDER_PRESETS.map((p) => p.id));
  const orphans = settings.providers.filter((p) => !presetIds.has(p.id));
  const rows = [
    ...PROVIDER_PRESETS.map((p) => ({ id: p.id, name: p.name })),
    ...orphans.map((p) => ({ id: p.id, name: presetName(p.id) })),
  ];

  const toggleProvider = (id: string, enabled: boolean) => {
    const cfg = findProvider(settings, id);
    if (!cfg || !cfg.apiKey.trim()) return; // 无 key 不可启用（按钮本身 disabled，双保险）
    const rest = settings.providers.filter((p) => p.id !== id);
    settingsStore.update({ providers: [...rest, { ...cfg, enabled }] });
  };

  return (
    <div className="card mt-4 flex flex-col gap-2 p-3">
      {rows.map((p) => {
        const cfg = findProvider(settings, p.id);
        const hasKey = !!cfg?.apiKey.trim();
        const on = effectiveEnabled(cfg);
        const n = cfg?.models.length ?? 0;
        return (
          <div key={p.id} className="card lift flex items-center gap-3 px-3 py-2.5">
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
              style={{
                background: "color-mix(in srgb, var(--accent) 10%, transparent)",
                color: "var(--accent)",
              }}
            >
              <ProviderIcon id={p.id} size={17} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-medium">{p.name}</div>
              <div className="text-xs" style={{ color: "var(--ink2)" }}>
                {S.settings.providerModels(n)}
                {!hasKey && ` · ${S.settings.fillKeyFirst}`}
              </div>
            </div>
            <Tooltip label={hasKey ? S.settings.providerEnable : S.settings.fillKeyFirst}>
              <span className="inline-flex">
                <Toggle
                  checked={on}
                  disabled={!hasKey}
                  onChange={(v) => toggleProvider(p.id, v)}
                />
              </span>
            </Tooltip>
            <Tooltip label={S.settings.secProviders} side="left">
              <button className="icon-btn" onClick={() => setDetailId(p.id)}>
                <Settings2 size={15} />
              </button>
            </Tooltip>
          </div>
        );
      })}
    </div>
  );
}

// ── ③ 选项 ──

function OptionsSection() {
  const settings = useSettings();
  const opts = settings.defaults;
  const [checking, setChecking] = useState(false);
  const [updateMsg, setUpdateMsg] = useState<{ tone: "ok" | "warn" | "err"; text: string; url?: string } | null>(null);
  const setOpts = (patch: Partial<ConvertOptions>) =>
    settingsStore.update({ defaults: { ...opts, ...patch } });

  const visionModels = activatedModels(settings, true);
  const allModels = activatedModels(settings, false);
  const modelOptions = (list: typeof allModels, tag: boolean) =>
    list.map((m) => ({
      value: m.ref,
      label: `${m.modelId} · ${presetName(m.providerId)}${tag && !m.vision ? `（${S.settings.visionNo}）` : ""}`,
    }));

  const pickDefaultOutput = async () => {
    const dir = await openDialog({ directory: true });
    if (typeof dir === "string") setOpts({ outputDir: dir });
  };

  const previewSound = async () => {
    try {
      const bytes = await invoke<number[]>("read_complete_sound");
      const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "audio/wav" }));
      await new Audio(url).play();
    } catch (e) {
      notify.error(String(e));
    }
  };

  const checkUpdate = async () => {
    setChecking(true);
    setUpdateMsg(null);
    try {
      const r = await invoke<{ status: string; latest?: string; url?: string; error?: string }>(
        "check_update",
      );
      if (r.status === "latest") {
        setUpdateMsg({ tone: "ok", text: S.settings.updateLatest });
        notify.success(S.settings.updateLatest);
      } else if (r.status === "update") {
        const text = S.settings.updateFound(r.latest ?? "");
        setUpdateMsg({ tone: "warn", text, url: r.url });
        notify.warning(text);
      } else {
        const text = S.settings.updateFailed(r.error ?? "");
        setUpdateMsg({ tone: "err", text });
        notify.error(text);
      }
    } catch (e) {
      const text = S.settings.updateFailed(String(e));
      setUpdateMsg({ tone: "err", text });
      notify.error(text);
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <Group title={S.settings.groupDefaults}>
        <Row label={S.settings.defaultMode}>
          <Select
            width={200}
            value={opts.mode}
            onChange={(v) => setOpts({ mode: v as ConvertOptions["mode"] })}
            options={[
              { value: "rule", label: S.convert.modeRule },
              { value: "vlm", label: S.convert.modeVlm },
            ]}
          />
        </Row>
        <Row label={S.settings.defaultOutputDir}>
          <div className="flex items-center gap-2">
            <input
              className="input mono"
              value={opts.outputDir}
              onChange={(e) => setOpts({ outputDir: e.target.value })}
            />
            <button className="btn shrink-0" onClick={pickDefaultOutput}>
              <FolderOpen size={14} /> {S.settings.browse}
            </button>
          </div>
        </Row>
        <Row label={S.settings.defaultLang}>
          <Select
            width={140}
            value={opts.translateLang}
            onChange={(v) => setOpts({ translateLang: v })}
            options={[
              { value: "zh", label: "中文" },
              { value: "en", label: "English" },
              { value: "ja", label: "日本語" },
            ]}
          />
        </Row>
        <Row label={S.settings.defaultWorkers}>
          <input
            type="number"
            min={1}
            max={8}
            className="input"
            value={opts.workers}
            onChange={(e) =>
              setOpts({ workers: Math.max(1, Math.min(8, Number(e.target.value) || 1)) })
            }
          />
        </Row>
        <Row label={S.settings.defaultReasoning}>
          <Select
            width={140}
            value={opts.reasoning}
            onChange={(v) => setOpts({ reasoning: v as ConvertOptions["reasoning"] })}
            options={[
              { value: "off", label: "off" },
              { value: "low", label: "low" },
              { value: "medium", label: "medium" },
              { value: "high", label: "high" },
            ]}
          />
        </Row>
        <Row label={S.settings.defaultVlmModel} desc={S.settings.visionOnlyHint}>
          <Select
            width={264}
            value={opts.vlmModel}
            onChange={(v) => setOpts({ vlmModel: v })}
            placeholder={S.settings.noModel}
            options={[{ value: "", label: S.settings.noModel }, ...modelOptions(visionModels, false)]}
          />
        </Row>
        <Row label={S.settings.defaultPostModel} desc={S.settings.postModelHint}>
          <Select
            width={264}
            value={opts.postModel}
            onChange={(v) => setOpts({ postModel: v })}
            placeholder={S.settings.noModel}
            options={[{ value: "", label: S.settings.noModel }, ...modelOptions(allModels, true)]}
          />
        </Row>
      </Group>

      <Group title={S.settings.groupAppearance}>
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              { id: "light", label: S.settings.themeLight, icon: Sun },
              { id: "dark", label: S.settings.themeDark, icon: Moon },
              { id: "system", label: S.settings.themeSystem, icon: Monitor },
            ] as const
          ).map((t) => {
            const active = settings.appearance.theme === t.id;
            return (
              <button
                key={t.id}
                className="card lift flex cursor-pointer flex-col items-center gap-1.5 p-3"
                style={{
                  borderColor: active ? "var(--accent)" : undefined,
                  boxShadow: active
                    ? "0 0 0 1px var(--accent), 0 0 16px color-mix(in srgb, var(--accent) 18%, transparent)"
                    : undefined,
                }}
                onClick={() =>
                  settingsStore.update({ appearance: { ...settings.appearance, theme: t.id } })
                }
              >
                <t.icon size={16} style={{ color: active ? "var(--accent)" : "var(--ink2)" }} />
                <span className="text-[14px]">{t.label}</span>
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2 pt-2 text-xs" style={{ color: "var(--ink2)" }}>
          {S.settings.zoomHint(Math.round(settings.appearance.zoom * 100))}
          {settings.appearance.zoom !== 1 && (
            <button
              className="btn btn-ghost cursor-pointer text-xs"
              style={{ color: "var(--accent)" }}
              onClick={() =>
                settingsStore.update({ appearance: { ...settings.appearance, zoom: 1 } })
              }
            >
              {S.settings.zoomReset}
            </button>
          )}
        </div>
      </Group>

      <Group title={S.settings.groupSound}>
        <Row label={S.settings.soundLabel}>
          <div className="flex items-center justify-end gap-2">
            <Tooltip label={S.settings.soundPreview}>
              <button className="icon-btn" onClick={previewSound}>
                <Play size={14} />
              </button>
            </Tooltip>
            <Toggle
              checked={settings.sound}
              onChange={(v) => settingsStore.update({ sound: v })}
            />
          </div>
        </Row>
      </Group>

      <Group title={S.settings.groupAbout}>
        <Row label={S.settings.version}>
          <span className="mono text-[14px]">v{APP_VERSION}</span>
        </Row>
        <Row label="Books Converter">
          <button className="btn" disabled={checking} onClick={checkUpdate}>
            <RefreshCw size={14} className={checking ? "animate-spin" : undefined} />
            {checking ? S.settings.checking : S.settings.checkUpdate}
          </button>
        </Row>
        {updateMsg && (
          <div
            className="flex items-center gap-2 pt-1 text-xs"
            style={{
              color:
                updateMsg.tone === "ok"
                  ? "var(--ok)"
                  : updateMsg.tone === "warn"
                    ? "var(--warn)"
                    : "var(--err)",
            }}
          >
            {updateMsg.text}
            {updateMsg.url && (
              <button
                className="cursor-pointer underline underline-offset-2"
                style={{ color: "var(--accent)" }}
                onClick={() => invoke("open_url", { url: updateMsg.url }).catch(() => {})}
              >
                {S.settings.updateDownload}
              </button>
            )}
          </div>
        )}
        <div className="pt-1 text-xs" style={{ color: "var(--ink2)" }}>
          {S.settings.license}
        </div>
      </Group>
    </>
  );
}

// ── 设置页壳：左侧子导航三块 ──

const SECTIONS = [
  { id: "parse", label: S.settings.secParse, icon: ScanText },
  { id: "providers", label: S.settings.secProviders, icon: BrainCircuit },
  { id: "options", label: S.settings.secOptions, icon: Settings2 },
] as const;

export function SettingsPage({
  section,
  providerId,
}: {
  section?: SettingsSection;
  providerId?: string;
}) {
  const [sec, setSec] = useState<SettingsSection>(section ?? "parse");
  const [detailId, setDetailId] = useState<string | null>(providerId ?? null);

  useEffect(() => {
    if (section) setSec(section);
    if (providerId !== undefined) setDetailId(providerId ?? null);
  }, [section, providerId]);

  return (
    <div className="mx-auto flex max-w-4xl gap-5 px-6 py-6 pb-10">
      <div className="w-40 shrink-0">
        <div className="sticky top-6 flex flex-col gap-1">
          {SECTIONS.map((s) => {
            const active = sec === s.id;
            return (
              <button
                key={s.id}
                className="flex cursor-pointer items-center gap-2 rounded-[10px] px-3 py-2 text-[14px] font-medium transition-all duration-150"
                style={{
                  background: active
                    ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                    : "transparent",
                  color: active ? "var(--accent)" : "var(--ink2)",
                }}
                onClick={() => {
                  setSec(s.id);
                  if (s.id !== "providers") setDetailId(null);
                }}
              >
                <s.icon size={15} />
                {s.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="min-w-0 flex-1">
        {sec === "parse" && <ParseSection />}
        {sec === "providers" && (
          <ProvidersSection detailId={detailId} setDetailId={setDetailId} />
        )}
        {sec === "options" && <OptionsSection />}
      </div>
    </div>
  );
}
