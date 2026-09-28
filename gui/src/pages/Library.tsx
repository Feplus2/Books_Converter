import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { notify } from "../lib/notify";
import {
  ArrowLeft,
  ExternalLink,
  FolderSymlink,
  ListX,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import { fileRowActions, registryDirsOf, type ProductFile, type RegistryItem } from "../lib/registry";
import { useSettings, type Settings } from "../lib/settings";
import { buildReconvertOptions } from "../lib/reconvert";
import { queueStore } from "../lib/queue";
import { navigate } from "../lib/nav";
import { S, engineName, formatElapsed } from "../lib/strings";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { ProductCard, fmtSize, fmtTime } from "../components/ProductCard";
import { Select } from "../components/Select";
import { Tooltip } from "../components/Tooltip";

/** 「再次转换」：用记录重放选项推进队列（高亮），跳转换页 */
function reconvertItem(item: RegistryItem, settings: Settings) {
  const registryDir = item.registry_path.replace(/[\\/][^\\/]+$/, "");
  const { options, modelFallback } = buildReconvertOptions(
    {
      engine: item.engine,
      ocr: item.ocr,
      translate: item.translate,
      formats: item.formats,
      vlm_model: item.vlm_model,
      vlm_reasoning: item.vlm_reasoning,
      registryDir,
    },
    settings,
  );
  queueStore.add([item.source_pdf], options, true, true);
  navigate({ page: "convert" });
  notify.success(S.library.toastReconvert(item.engine));
  if (modelFallback) {
    notify.warning(S.library.toastModelFallback(item.vlm_model ?? ""));
  }
}

function ReconvertButton({ item, settings }: { item: RegistryItem; settings: Settings }) {
  const ok = item.source_exists;
  return (
    <Tooltip label={ok ? S.library.reconvertTooltip : S.library.sourceMissing(item.source_pdf)}>
      <span className="inline-flex">
        <button
          className="icon-btn"
          disabled={!ok}
          onClick={(e) => {
            e.stopPropagation();
            if (ok) reconvertItem(item, settings);
          }}
        >
          <RotateCcw size={15} />
        </button>
      </span>
    </Tooltip>
  );
}

/** 卡片级「删除记录」（病例 052）：两段确认，文案明示只删登记记录、不动产物文件 */
function RemoveEntryButton({ item, onChanged }: { item: RegistryItem; onChanged: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const remove = async () => {
    try {
      await invoke("remove_entry", { registryPath: item.registry_path, ts: item.ts });
      notify.success(S.library.toastRemoved);
      onChanged();
    } catch (e) {
      notify.error(String(e));
    }
  };
  if (confirming) {
    return (
      <span className="card flex items-center gap-1.5 rounded-md px-2 py-1">
        <span className="text-xs whitespace-nowrap" style={{ color: "var(--warn)" }}>
          {S.library.removeConfirmFiles}
        </span>
        <button
          className="text-xs font-medium"
          style={{ color: "var(--err)" }}
          onClick={(e) => {
            e.stopPropagation();
            void remove();
          }}
        >
          {S.common.confirm}
        </button>
        <button
          className="text-xs"
          style={{ color: "var(--ink2)" }}
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(false);
          }}
        >
          {S.common.cancel}
        </button>
      </span>
    );
  }
  return (
    <Tooltip label={S.library.removeTooltip}>
      <button
        className="icon-btn"
        onClick={(e) => {
          e.stopPropagation();
          setConfirming(true);
        }}
      >
        <ListX size={15} />
      </button>
    </Tooltip>
  );
}

export function LibraryPage() {
  const settings = useSettings();
  const [items, setItems] = useState<RegistryItem[]>([]);
  const [search, setSearch] = useState("");
  const [fmtFilter, setFmtFilter] = useState("");
  const [engineFilter, setEngineFilter] = useState("");
  const [detail, setDetail] = useState<RegistryItem | null>(null);

  // 病例 053 纯登记制：产物库只读登记文件（outputDir + historyDirs 里的
  // _registry.jsonl）。不做目录扫描——扫描曾把通用目录（如 D:\temp_files）
  // 里的本机文档误判进产物库；用户自己挪/删的文件显示「丢失」徽标，不追踪。
  const registryDirs = useMemo(() => registryDirsOf(settings), [settings]);

  const refresh = useCallback(async () => {
    try {
      const reg = registryDirs.length
        ? await invoke<RegistryItem[]>("read_registry", { dirs: registryDirs })
        : [];
      setItems(reg);
    } catch (e) {
      notify.error(String(e));
    }
  }, [registryDirs]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const engines = [...new Set(items.map((i) => i.engine).filter(Boolean))];

  const filtered = items.filter((i) => {
    if (search && !i.title.toLowerCase().includes(search.toLowerCase())) return false;
    if (fmtFilter && !i.formats.includes(fmtFilter)) return false;
    if (engineFilter && i.engine !== engineFilter) return false;
    return true;
  });

  // ── 详情子视图 ──
  if (detail) {
    return (
      <DetailView
        item={detail}
        onBack={() => {
          setDetail(null);
          void refresh();
        }}
      />
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <Search
            size={14}
            className="absolute top-1/2 left-3 -translate-y-1/2"
            style={{ color: "var(--ink2)" }}
          />
          <input
            className="input pl-9"
            placeholder={S.library.search}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select
          width={120}
          value={fmtFilter}
          onChange={setFmtFilter}
          options={[
            { value: "", label: S.library.filterFormat },
            { value: "epub", label: "EPUB" },
            { value: "md", label: "MD" },
            { value: "tex", label: "TEX" },
          ]}
        />
        <Select
          width={140}
          value={engineFilter}
          onChange={setEngineFilter}
          options={[
            { value: "", label: S.library.filterEngine },
            ...engines.map((e) => ({ value: e, label: engineName(e) })),
          ]}
        />
        <Tooltip label={S.library.refreshTooltip}>
          <button
            className="icon-btn"
            onClick={() => {
              void refresh();
              notify.success(S.library.toastRefreshed);
            }}
          >
            <RefreshCw size={15} />
          </button>
        </Tooltip>
      </div>

      {/* 卡片流 */}
      {filtered.length ? (
        <div className="mt-4 grid grid-cols-2 gap-3 pb-8">
          {filtered.map((i) => (
            <ProductCard
              key={`${i.registry_path}:${i.ts}`}
              item={i}
              onOpen={() => setDetail(i)}
              action={
                <span className="flex items-center gap-1">
                  <ReconvertButton item={i} settings={settings} />
                  <RemoveEntryButton item={i} onChanged={() => void refresh()} />
                </span>
              }
            />
          ))}
        </div>
      ) : (
        <EmptyState kind="library" />
      )}
    </div>
  );
}

// ── 详情子视图 ──

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-3 py-1">
      <span className="w-20 shrink-0 text-xs" style={{ color: "var(--ink2)" }}>
        {label}
      </span>
      <span className={`min-w-0 flex-1 truncate text-[14px] ${mono ? "mono" : ""}`} title={value}>
        {value || "—"}
      </span>
    </div>
  );
}

function FileRow({ file }: { file: ProductFile }) {
  return (
    <div
      className="card lift flex items-center gap-3 px-3 py-2"
      style={file.exists ? undefined : { opacity: 0.55 }}
    >
      <Badge tone="muted">{file.fmt.toUpperCase()}</Badge>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[14px] font-medium">
          {file.name}
          {file.is_dir ? "/" : ""}
        </div>
        <div className="mono truncate text-xs" style={{ color: "var(--ink2)" }} title={file.path}>
          {file.exists ? fmtSize(file.size) : S.library.badgeMissing} · {file.path}
        </div>
      </div>
      {file.exists && (
        <>
          <Tooltip label={S.library[fileRowActions(file).openTooltip]}>
            <button
              className="icon-btn"
              onClick={() => invoke("open_file", { path: file.path }).catch((e) => notify.error(String(e)))}
            >
              <ExternalLink size={14} />
            </button>
          </Tooltip>
          {/* 目录行不渲染 reveal：与 open 等价且旧实现（explorer /select 目录）
              必翻车回落「文档」主文件夹（病例 057） */}
          {fileRowActions(file).showReveal && (
            <Tooltip label={S.library.reveal}>
              <button
                className="icon-btn"
                onClick={() =>
                  invoke("reveal_in_explorer", { path: file.path }).catch((e) => notify.error(String(e)))
                }
              >
                <FolderSymlink size={14} />
              </button>
            </Tooltip>
          )}
        </>
      )}
    </div>
  );
}

function DetailView({ item, onBack }: { item: RegistryItem; onBack: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const settings = useSettings();

  const remove = async () => {
    try {
      await invoke("remove_entry", { registryPath: item.registry_path, ts: item.ts });
      notify.success(S.library.toastRemoved);
      onBack();
    } catch (e) {
      notify.error(String(e));
    }
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <button className="btn btn-ghost" onClick={onBack}>
          <ArrowLeft size={14} /> {S.library.back}
        </button>
        <span className="flex items-center gap-2">
          <Tooltip label={item.source_exists ? S.library.reconvertTooltip : S.library.sourceMissing(item.source_pdf)}>
            <span className="inline-flex">
              <button
                className="btn"
                disabled={!item.source_exists}
                onClick={() => reconvertItem(item, settings)}
              >
                <RotateCcw size={14} /> {S.library.reconvert}
              </button>
            </span>
          </Tooltip>
          {confirming ? (
            <span className="flex items-center gap-2">
              <span className="text-xs" style={{ color: "var(--warn)" }}>
                {S.library.removeConfirmFiles}
              </span>
              <button className="btn" style={{ color: "var(--err)" }} onClick={remove}>
                {S.common.confirm}
              </button>
              <button className="btn btn-ghost" onClick={() => setConfirming(false)}>
                {S.common.cancel}
              </button>
            </span>
          ) : (
            <Tooltip label={S.library.removeTooltip}>
              <button className="icon-btn" onClick={() => setConfirming(true)}>
                <ListX size={15} />
              </button>
            </Tooltip>
          )}
        </span>
      </div>

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-[15px] font-semibold">{item.title}</span>
          {item.missing && <Badge tone="err">{S.library.badgeMissing}</Badge>}
        </div>

        <div className="mb-1 text-xs font-semibold" style={{ color: "var(--ink2)" }}>
          {S.library.metaTitle}
        </div>
        <MetaRow label={S.library.metaTime} value={fmtTime(item.ts)} />
        <MetaRow label={S.library.metaEngine} value={engineName(item.engine)} />
        <MetaRow label={S.library.metaFormats} value={item.formats.join(", ").toUpperCase()} />
        <MetaRow label={S.library.metaOcr} value={item.ocr ? S.library.yes : S.library.no} />
        <MetaRow label={S.library.metaTranslate} value={item.translate ?? S.library.no} />
        <MetaRow label={S.library.metaElapsed} value={formatElapsed(item.elapsed_s)} />
        <MetaRow label={S.library.metaVersion} value={`v${item.app_version}`} />
        <MetaRow label={S.library.metaSource} value={item.source_pdf} mono />
        <MetaRow label={S.library.metaWorkDir} value={item.work_dir} mono />

        <div className="mt-4 mb-2 text-xs font-semibold" style={{ color: "var(--ink2)" }}>
          {S.library.filesTitle}
        </div>
        <div className="flex flex-col gap-2">
          {item.files.map((f) => (
            <FileRow key={`${f.fmt}:${f.path}`} file={f} />
          ))}
        </div>
      </div>
    </div>
  );
}
