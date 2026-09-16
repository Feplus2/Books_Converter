import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { notify } from "../lib/notify";
import {
  ArrowLeft,
  ExternalLink,
  FileQuestion,
  FolderSearch,
  FolderSymlink,
  ListX,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import type { ProductFile, RegistryItem, UnregisteredItem } from "../lib/registry";
import { settingsStore, useSettings } from "../lib/settings";
import { S, formatElapsed } from "../lib/strings";
import { Badge } from "../components/Badge";
import { ProductCard, fmtSize, fmtTime } from "../components/ProductCard";
import { Select } from "../components/Select";
import { Tooltip } from "../components/Tooltip";

type Detail =
  | { kind: "registered"; item: RegistryItem }
  | { kind: "unregistered"; item: UnregisteredItem };

export function LibraryPage() {
  const settings = useSettings();
  const [items, setItems] = useState<RegistryItem[]>([]);
  const [unregistered, setUnregistered] = useState<UnregisteredItem[]>([]);
  const [search, setSearch] = useState("");
  const [fmtFilter, setFmtFilter] = useState("");
  const [engineFilter, setEngineFilter] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);

  const knownDirs = useMemo(() => {
    const all = [
      settings.defaults.outputDir,
      ...settings.scanDirs,
      ...settings.historyDirs,
    ].filter(Boolean);
    return [...new Set(all)];
  }, [settings]);

  const refresh = useCallback(async () => {
    if (!knownDirs.length) {
      setItems([]);
      setUnregistered([]);
      return;
    }
    try {
      const [reg, unreg] = await Promise.all([
        invoke<RegistryItem[]>("read_registry", { dirs: knownDirs }),
        invoke<UnregisteredItem[]>("scan_unregistered", { dirs: knownDirs }),
      ]);
      setItems(reg);
      setUnregistered(unreg);
    } catch (e) {
      notify.error(String(e));
    }
  }, [knownDirs]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const engines = useMemo(() => [...new Set(items.map((i) => i.engine).filter(Boolean))], [items]);

  const filtered = items.filter((i) => {
    if (search && !i.title.toLowerCase().includes(search.toLowerCase())) return false;
    if (fmtFilter && !i.formats.includes(fmtFilter)) return false;
    if (engineFilter && i.engine !== engineFilter) return false;
    return true;
  });
  const filteredUnreg = unregistered.filter((i) => {
    if (search && !i.name.toLowerCase().includes(search.toLowerCase())) return false;
    if (fmtFilter && i.fmt !== fmtFilter) return false;
    if (engineFilter) return false;
    return true;
  });

  const addScanDir = async () => {
    const dir = await openDialog({ directory: true });
    if (typeof dir === "string" && !settings.scanDirs.includes(dir)) {
      settingsStore.update({ scanDirs: [...settings.scanDirs, dir] });
      notify.success(S.library.toastRefreshed);
    }
  };

  // ── 详情子视图 ──
  if (detail) {
    return (
      <DetailView
        detail={detail}
        onBack={() => {
          setDetail(null);
          void refresh();
        }}
        onChanged={() => void refresh()}
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
            ...engines.map((e) => ({ value: e, label: e })),
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
        <Tooltip label={S.library.addScanDirTooltip}>
          <button className="icon-btn" onClick={addScanDir}>
            <FolderSearch size={15} />
          </button>
        </Tooltip>
      </div>

      {/* 扫描目录 chips */}
      {settings.scanDirs.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-xs" style={{ color: "var(--ink2)" }}>
            {S.library.scanDirs}:
          </span>
          {settings.scanDirs.map((d) => (
            <span key={d} className="card flex items-center gap-1 rounded-md px-2 py-0.5 text-xs">
              <span className="mono max-w-64 truncate" style={{ color: "var(--ink2)" }}>
                {d}
              </span>
              <button
                className="icon-btn"
                style={{ width: 20, height: 20 }}
                onClick={() =>
                  settingsStore.update({
                    scanDirs: settings.scanDirs.filter((x) => x !== d),
                  })
                }
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* 卡片流 */}
      {filtered.length + filteredUnreg.length ? (
        <div className="mt-4 grid grid-cols-2 gap-3 pb-8">
          {filtered.map((i) => (
            <ProductCard
              key={`${i.registry_path}:${i.ts}`}
              item={i}
              onOpen={() => setDetail({ kind: "registered", item: i })}
            />
          ))}
          {filteredUnreg.map((i) => (
            <button
              key={i.path}
              className="card lift w-full cursor-pointer p-4 text-left"
              onClick={() => setDetail({ kind: "unregistered", item: i })}
            >
              <div className="flex items-start gap-3">
                <div
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                  style={{
                    background: "color-mix(in srgb, var(--ink) 8%, transparent)",
                    color: "var(--ink2)",
                  }}
                >
                  <FileQuestion size={18} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{i.name}</div>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <Badge tone="muted">{i.fmt.toUpperCase()}</Badge>
                    <Badge tone="warn">{S.library.badgeUnregistered}</Badge>
                  </div>
                  <div className="mono mt-1.5 truncate text-xs" style={{ color: "var(--ink2)" }}>
                    {fmtSize(i.size)}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="py-16 text-center text-xs" style={{ color: "var(--ink2)" }}>
          {S.library.empty}
        </div>
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

function FileRow({
  file,
  registryPath,
  ts,
  onChanged,
}: {
  file: ProductFile;
  registryPath?: string;
  ts?: string;
  onChanged: () => void;
}) {
  const relocate = async () => {
    const selected = await openDialog(file.is_dir ? { directory: true } : { multiple: false });
    if (typeof selected !== "string") return;
    try {
      await invoke("relocate_entry", {
        registryPath,
        ts,
        fmt: file.fmt,
        oldPath: file.path,
        newPath: selected,
      });
      notify.success(S.library.toastRelocated);
      onChanged();
    } catch (e) {
      notify.error(String(e));
    }
  };

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
      {file.exists ? (
        <>
          <Tooltip label={S.library.open}>
            <button
              className="icon-btn"
              onClick={() => invoke("open_file", { path: file.path }).catch((e) => notify.error(String(e)))}
            >
              <ExternalLink size={14} />
            </button>
          </Tooltip>
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
        </>
      ) : (
        registryPath && (
          <Tooltip label={S.library.relocateTooltip}>
            <button className="btn" onClick={relocate}>
              {S.library.relocate}
            </button>
          </Tooltip>
        )
      )}
    </div>
  );
}

function DetailView({
  detail,
  onBack,
  onChanged,
}: {
  detail: Detail;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  if (detail.kind === "unregistered") {
    const i = detail.item;
    return (
      <div className="mx-auto max-w-3xl px-6 py-6">
        <button className="btn btn-ghost mb-4" onClick={onBack}>
          <ArrowLeft size={14} /> {S.library.back}
        </button>
        <div className="card p-5">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[15px] font-semibold">{i.name}</span>
            <Badge tone="warn">{S.library.badgeUnregistered}</Badge>
          </div>
          <div className="mb-4 text-xs" style={{ color: "var(--ink2)" }}>
            {S.library.unregisteredHint}
          </div>
          <MetaRow label={S.library.metaFormats} value={i.fmt.toUpperCase()} />
          <MetaRow label="大小" value={fmtSize(i.size)} />
          <MetaRow label="路径" value={i.path} mono />
          <div className="mt-4 flex gap-2">
            <button
              className="btn btn-primary"
              onClick={() => invoke("open_file", { path: i.path }).catch((e) => notify.error(String(e)))}
            >
              <ExternalLink size={14} /> {S.library.open}
            </button>
            <button
              className="btn"
              onClick={() =>
                invoke("reveal_in_explorer", { path: i.path }).catch((e) => notify.error(String(e)))
              }
            >
              <FolderSymlink size={14} /> {S.library.reveal}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const item = detail.item;
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
        {confirming ? (
          <span className="flex items-center gap-2">
            <span className="text-xs" style={{ color: "var(--warn)" }}>
              {S.library.removeConfirm}
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
        <MetaRow label={S.library.metaEngine} value={item.engine} />
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
            <FileRow
              key={`${f.fmt}:${f.path}`}
              file={f}
              registryPath={item.registry_path}
              ts={item.ts}
              onChanged={onChanged}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
