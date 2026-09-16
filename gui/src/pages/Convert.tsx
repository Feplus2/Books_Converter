import { useEffect, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { CloudUpload, FolderOpen, Play, ScanText, Sparkles, Trash2 } from "lucide-react";
import { queueStore, useQueue } from "../lib/queue";
import {
  activatedModels,
  reasoningSupported,
  settingsStore,
  useSettings,
  type ConvertOptions,
} from "../lib/settings";
import { presetName } from "../lib/providers";
import { notify } from "../lib/notify";
import { navigate } from "../lib/nav";
import { S } from "../lib/strings";
import { QueueItem } from "../components/QueueItem";
import { EmptyState } from "../components/EmptyState";
import { Select } from "../components/Select";
import { Toggle } from "../components/Toggle";
import { Tooltip } from "../components/Tooltip";

const FORMATS = ["epub", "md", "tex"] as const;

function Field({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) {
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
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function MissingHint({ onGo }: { onGo: () => void }) {
  return (
    <button
      className="ml-1 cursor-pointer text-xs underline underline-offset-2"
      style={{ color: "var(--accent)" }}
      onClick={onGo}
    >
      {S.convert.goSettings}
    </button>
  );
}

export function ConvertPage() {
  const settings = useSettings();
  const tasks = useQueue();
  const [dragging, setDragging] = useState(false);
  const dragCounter = useRef(0);

  const opts = settings.defaults;
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const setOpts = (patch: Partial<ConvertOptions>) =>
    settingsStore.update({ defaults: { ...opts, ...patch } });

  const enqueueRef = useRef<(paths: string[]) => void>(() => {});
  enqueueRef.current = (paths: string[]) => {
    const o = optsRef.current;
    const pdfs = paths.filter((p) => p.toLowerCase().endsWith(".pdf"));
    if (pdfs.length < paths.length) notify.warning(S.convert.notPdf);
    if (!pdfs.length) return;
    if (!o.formats.length) {
      notify.warning(S.convert.needFormat);
      return;
    }
    queueStore.add(pdfs, o);
    notify.success(S.convert.toastEnqueued(pdfs.length));
  };

  // 拖放订阅一次到底，经 ref 取最新选项（不再随选项变更重订阅）
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        const t = event.payload.type;
        if (t === "enter") {
          dragCounter.current++;
          setDragging(true);
        } else if (t === "leave") {
          dragCounter.current = Math.max(0, dragCounter.current - 1);
          if (!dragCounter.current) setDragging(false);
        } else if (t === "drop") {
          dragCounter.current = 0;
          setDragging(false);
          enqueueRef.current(event.payload.paths);
        }
      })
      .then((u) => (unlisten = u));
    return () => unlisten?.();
  }, []);

  const pickFiles = async () => {
    const selected = await openDialog({
      multiple: true,
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    if (selected) enqueueRef.current(Array.isArray(selected) ? selected : [selected]);
  };

  const pickOutputDir = async () => {
    const dir = await openDialog({ directory: true });
    if (typeof dir === "string") setOpts({ outputDir: dir });
  };

  const visionModels = activatedModels(settings, true);
  const allModels = activatedModels(settings, false);
  const mineruOk = !!settings.ocr.mineruToken.trim();
  const paddleOk = !!settings.ocr.paddleocrToken.trim();

  const hasQueued = tasks.some((t) => t.status === "queued");
  const hasFinished = tasks.some((t) => t.status !== "queued" && t.status !== "running");

  const goParse = () => navigate({ page: "settings", section: "parse" });
  const goProviders = () => navigate({ page: "settings", section: "providers" });

  const modelOptions = (list: typeof visionModels, visionTag: boolean) =>
    list.map((m) => ({
      value: m.ref,
      label: `${m.modelId} · ${presetName(m.providerId)}${visionTag && !m.vision ? `（${S.settings.visionNo}）` : ""}`,
    }));

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      {/* 拖放区 */}
      <div
        className="card lift flex cursor-pointer flex-col items-center justify-center gap-2 px-6 py-10 text-center"
        style={{
          borderStyle: "dashed",
          borderWidth: 1.5,
          borderColor: dragging ? "var(--accent)" : "color-mix(in srgb, var(--ink) 20%, transparent)",
          background: dragging ? "color-mix(in srgb, var(--accent) 6%, transparent)" : "var(--card)",
        }}
        onClick={pickFiles}
      >
        <CloudUpload size={28} style={{ color: "var(--accent)" }} />
        <div className="text-[15px] font-medium">
          {dragging ? S.convert.dropActive : S.convert.dropTitle}
        </div>
        <div className="text-xs" style={{ color: "var(--ink2)" }}>
          {S.convert.dropHint} · {S.convert.dropBrowse}
        </div>
      </div>

      {/* 转换选项 */}
      <div className="card mt-4 p-4">
        <div className="mb-1 text-[14px] font-semibold" style={{ color: "var(--ink2)" }}>
          {S.convert.optionsTitle}
        </div>

        {/* 模式选择 */}
        <div className="grid grid-cols-2 gap-2 py-2">
          {(
            [
              { id: "rule", label: S.convert.modeRule, desc: S.convert.modeRuleDesc, icon: ScanText, isNew: false },
              { id: "vlm", label: S.convert.modeVlm, desc: S.convert.modeVlmDesc, icon: Sparkles, isNew: true },
            ] as const
          ).map((m) => {
            const active = opts.mode === m.id;
            return (
              <button
                key={m.id}
                className="card lift relative cursor-pointer p-3 text-left"
                style={{
                  borderColor: active ? "var(--accent)" : undefined,
                  boxShadow: active
                    ? "0 0 0 1px var(--accent), 0 0 16px color-mix(in srgb, var(--accent) 18%, transparent)"
                    : undefined,
                }}
                onClick={() => setOpts({ mode: m.id })}
              >
                {m.isNew && (
                  <span
                    className="absolute -top-2 right-2 rounded-full px-1.5 py-px text-[10px] font-bold tracking-wide"
                    style={{
                      color: "var(--accent)",
                      border: "1px solid var(--accent)",
                      background: "var(--card)",
                    }}
                  >
                    {S.convert.newBadge}
                  </span>
                )}
                <div className="flex items-center gap-1.5 text-[14px] font-semibold">
                  <m.icon size={14} style={{ color: active ? "var(--accent)" : "var(--ink2)" }} />
                  {m.label}
                </div>
                <div className="mt-1 text-xs leading-4" style={{ color: "var(--ink2)" }}>
                  {m.desc}
                </div>
              </button>
            );
          })}
        </div>

        {opts.mode === "rule" ? (
          <>
            <Field label={S.convert.parseModel}>
              <div className="flex items-center gap-1">
                <Select
                  width={264}
                  value={opts.ruleEngine}
                  onChange={(v) => setOpts({ ruleEngine: v as "mineru" | "paddleocr" })}
                  options={[
                    {
                      value: "mineru",
                      label: `${S.convert.parseModelMineru}（${S.convert.parseModelMineruDesc}）`,
                      disabled: !mineruOk,
                      disabledReason: S.convert.tokenMissing,
                    },
                    {
                      value: "paddleocr",
                      label: `${S.convert.parseModelPaddleocr}（${S.convert.parseModelPaddleocrDesc}）`,
                      disabled: !paddleOk,
                      disabledReason: S.convert.tokenMissing,
                    },
                  ]}
                />
                {((opts.ruleEngine === "mineru" && !mineruOk) ||
                  (opts.ruleEngine === "paddleocr" && !paddleOk)) && (
                  <MissingHint onGo={goParse} />
                )}
              </div>
            </Field>
            <Field label={S.convert.ocrLabel} desc={S.convert.ocrDesc}>
              <Toggle checked={opts.ocr} onChange={(v) => setOpts({ ocr: v })} />
            </Field>
            <Field label={S.convert.postModel} desc={S.convert.postModelDesc}>
              <Select
                width={264}
                value={opts.postModel}
                onChange={(v) => setOpts({ postModel: v })}
                placeholder={S.settings.noModel}
                options={[
                  { value: "", label: S.settings.noModel },
                  ...modelOptions(allModels, true),
                ]}
              />
            </Field>
          </>
        ) : (
          <>
            <Field label={S.convert.vlmModel}>
              {visionModels.length ? (
                <Select
                  width={264}
                  value={opts.vlmModel}
                  onChange={(v) => setOpts({ vlmModel: v })}
                  placeholder={S.settings.noModel}
                  options={[
                    { value: "", label: S.settings.noModel },
                    ...modelOptions(visionModels, false),
                  ]}
                />
              ) : (
                <span className="text-xs" style={{ color: "var(--warn)" }}>
                  {S.convert.vlmModelEmpty}
                  <button
                    className="ml-1 cursor-pointer underline underline-offset-2"
                    style={{ color: "var(--accent)" }}
                    onClick={goProviders}
                  >
                    {S.convert.goActivate}
                  </button>
                </span>
              )}
            </Field>
            <div
              className="py-1 text-xs leading-5"
              style={{ color: "var(--warn)" }}
            >
              {S.convert.vlmModelHint}
            </div>
            {reasoningSupported(settings, opts.vlmModel) ? (
              <Field label={S.convert.reasoning}>
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
              </Field>
            ) : (
              <Field label={S.convert.reasoning}>
                <span className="text-xs" style={{ color: "var(--ink2)" }}>
                  {S.convert.reasoningNa}
                </span>
              </Field>
            )}
            <Field label={S.convert.workers}>
              <input
                type="number"
                min={1}
                max={8}
                className="input w-20"
                value={opts.workers}
                onChange={(e) =>
                  setOpts({ workers: Math.max(1, Math.min(8, Number(e.target.value) || 1)) })
                }
              />
            </Field>
          </>
        )}

        <Field label={S.convert.translateLabel} desc={S.convert.translateDesc}>
          <div className="flex items-center gap-2">
            {opts.translate && (
              <Select
                width={120}
                value={opts.translateLang}
                onChange={(v) => setOpts({ translateLang: v })}
                options={[
                  { value: "zh", label: "中文" },
                  { value: "en", label: "English" },
                  { value: "ja", label: "日本語" },
                ]}
              />
            )}
            <Toggle checked={opts.translate} onChange={(v) => setOpts({ translate: v })} />
          </div>
        </Field>

        <Field label={S.convert.outputDir}>
          <div className="flex items-center gap-2">
            <span
              className="mono max-w-56 truncate text-xs"
              style={{ color: "var(--ink2)" }}
              title={opts.outputDir}
            >
              {opts.outputDir || S.convert.outputDirEmpty}
            </span>
            <button className="btn" onClick={pickOutputDir}>
              <FolderOpen size={14} /> {S.convert.outputDirPick}
            </button>
          </div>
        </Field>

        <Field label={S.convert.formatLabel}>
          <div className="flex gap-1.5">
            {FORMATS.map((f) => {
              const active = opts.formats.includes(f);
              return (
                <button
                  key={f}
                  className="btn"
                  style={
                    active
                      ? {
                          background: "color-mix(in srgb, var(--accent) 12%, transparent)",
                          borderColor: "var(--accent)",
                          color: "var(--accent)",
                        }
                      : undefined
                  }
                  onClick={() =>
                    setOpts({
                      formats: active
                        ? opts.formats.filter((x) => x !== f)
                        : [...opts.formats, f],
                    })
                  }
                >
                  {f.toUpperCase()}
                </button>
              );
            })}
          </div>
        </Field>

        {opts.formats.includes("md") && (
          <>
            <Field label={S.convert.mdSplit} desc={S.convert.mdSplitDesc}>
              <Toggle checked={opts.mdSplit} onChange={(v) => setOpts({ mdSplit: v })} />
            </Field>
            <Field label={S.convert.mdDialect}>
              <Select
                width={140}
                value={opts.mdDialect}
                onChange={(v) => setOpts({ mdDialect: v as "gfm" | "pandoc" })}
                options={[
                  { value: "gfm", label: "GFM" },
                  { value: "pandoc", label: "Pandoc" },
                ]}
              />
            </Field>
          </>
        )}

        {opts.formats.includes("tex") && (
          <>
            <Field label={S.convert.texFull} desc={S.convert.texFullDesc}>
              <Toggle checked={opts.texFull} onChange={(v) => setOpts({ texFull: v })} />
            </Field>
            <Field label={S.convert.exportLang}>
              <Select
                width={200}
                value={opts.exportLang}
                onChange={(v) =>
                  setOpts({ exportLang: v as ConvertOptions["exportLang"] })
                }
                options={[
                  { value: "auto", label: S.convert.exportLangAuto },
                  { value: "orig", label: S.convert.exportLangOrig },
                  { value: "trans", label: S.convert.exportLangTrans },
                  { value: "both", label: S.convert.exportLangBoth },
                ]}
              />
            </Field>
          </>
        )}
      </div>

      {/* 队列 */}
      <div className="mt-6 mb-2 flex items-center justify-between">
        <div className="text-[14px] font-semibold" style={{ color: "var(--ink2)" }}>
          {S.convert.queueTitle}
        </div>
        <div className="flex items-center gap-2">
          {hasFinished && (
            <Tooltip label={S.convert.clearFinished}>
              <button className="icon-btn" onClick={() => queueStore.clearFinished()}>
                <Trash2 size={15} />
              </button>
            </Tooltip>
          )}
          <button
            className="btn btn-primary"
            disabled={!hasQueued}
            onClick={() => void queueStore.startAll()}
          >
            <Play size={14} /> {S.convert.startAll}
          </button>
        </div>
      </div>
      {tasks.length ? (
        <div className="flex flex-col gap-2 pb-8">
          {tasks.map((t) => (
            <QueueItem key={t.id} task={t} />
          ))}
        </div>
      ) : (
        <EmptyState kind="queue" />
      )}
    </div>
  );
}
