// 转换队列：模块级 store（跨页面存活）。拖入只入队为「待开始」，点【开始转换】才开跑；
// 并发上限 1，开跑后自动接力，跑空自动熄火（再次拖入需重新点开始）。
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useSyncExternalStore } from "react";
import { parsePipelineEvent, type PipelineEvent } from "./protocol";
import {
  buildCliArgs,
  buildEnv,
  settingsStore,
  type ConvertOptions,
} from "./settings";
import { precheckTask } from "./precheck";
import { humanizeError } from "./errors";
import { notify } from "./notify";
import { navigate } from "./nav";
import { S } from "./strings";

export type TaskStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface QueueTask {
  id: string;
  pdfPath: string;
  title: string;
  options: ConvertOptions;
  status: TaskStatus;
  percent: number;
  stage: number | null;
  stageName: string;
  stagesTotal: number;
  logs: string[];
  error?: string; // 人话标题
  errorDetail?: string; // 原文（技术细节）
  epubPath?: string;
  elapsed?: number;
  exitCode?: number | null;
  lastEventAt: number;
  stalled: boolean;
  cancelAskedAt?: number;
  highlight?: boolean; // 再次转换入队闪烁
}

type Listener = () => void;

const STALL_MS = 3 * 60 * 1000;
const CANCEL_WATCHDOG_MS = 6000;

let seq = 0;

class QueueStore {
  tasks: QueueTask[] = [];
  /** 队列是否已被【开始转换】点火；跑空自动熄火 */
  started = false;
  private listeners = new Set<Listener>();
  private unlisteners = new Map<string, UnlistenFn[]>();

  constructor() {
    // 停滞巡检：只提示，绝不自动动作（铁律 0）
    setInterval(() => this.checkStall(), 30_000);
  }

  add(paths: string[], options: ConvertOptions, highlight = false) {
    for (const pdfPath of paths) {
      const name = pdfPath.split(/[\\/]/).pop() ?? pdfPath;
      this.tasks.push({
        id: `t${Date.now()}-${seq++}`,
        pdfPath,
        title: name.replace(/\.pdf$/i, ""),
        options: { ...options, formats: [...options.formats] },
        status: "queued",
        percent: 0,
        stage: null,
        stageName: "",
        stagesTotal: options.translate ? 4 : 3,
        logs: [],
        lastEventAt: Date.now(),
        stalled: false,
        highlight,
      });
    }
    if (highlight) {
      // 「再次转换」入队高亮：2s 后自动熄灭
      setTimeout(() => {
        this.tasks.forEach((t) => (t.highlight = false));
        this.emit();
      }, 2200);
    }
    this.emit();
  }

  hasQueued(): boolean {
    return this.tasks.some((t) => t.status === "queued");
  }

  /** 【开始转换】：预检 → 输出目录兜底 → 点火 → 接力 */
  async startAll() {
    const settings = settingsStore.settings;
    let defaultDir = "";
    for (const task of this.tasks) {
      if (task.status !== "queued") continue;
      // ① 缺 key 预检：不启动该项 + 人话 toast 指明缺哪个 key + 去设置
      const check = precheckTask(task.options, settings);
      if (!check.ok) {
        notify.error(S.convert.toastMissingKey(check.missing), {
          action: {
            label: S.convert.goSettings,
            onClick: () =>
              navigate({
                page: "settings",
                section: check.section,
                providerId: check.providerId,
              }),
          },
        });
        continue;
      }
      // ② 输出目录兜底：为空则用默认建议值并告知（拖入永不拦截）
      if (!task.options.outputDir.trim()) {
        if (!defaultDir) {
          defaultDir = await invoke<string>("default_output_dir").catch(() => "");
        }
        if (defaultDir) {
          task.options.outputDir = defaultDir;
          notify.info(S.convert.toastDefaultDir(defaultDir));
        }
      }
    }
    if (!this.hasQueued()) {
      this.emit();
      return;
    }
    this.started = true;
    this.emit();
    void this.pump();
  }

  private runningTask(): QueueTask | undefined {
    return this.tasks.find((t) => t.status === "running");
  }

  private async pump() {
    if (!this.started) return;
    if (this.runningTask()) return;
    const next = this.tasks.find((t) => t.status === "queued");
    if (!next) {
      // 队列跑空：熄火。之后新拖入的书需重新点【开始转换】
      this.started = false;
      this.emit();
      return;
    }
    const task = next;
    task.status = "running";
    task.lastEventAt = Date.now();
    task.stalled = false;
    this.emit();

    const unls: UnlistenFn[] = [];
    this.unlisteners.set(task.id, unls);
    unls.push(
      await listen<string>(`conversion::${task.id}`, (e) => {
        const ev = parsePipelineEvent(e.payload);
        if (!ev) return;
        this.onEvent(task, ev);
      }),
    );
    unls.push(
      await listen<string>(`conversion::${task.id}::exit`, (e) => {
        let code: number | null = null;
        try {
          code = JSON.parse(e.payload)?.code ?? null;
        } catch {
          /* ignore */
        }
        this.onExit(task, code);
      }),
    );

    const settings = settingsStore.settings;
    try {
      await invoke("start_conversion", {
        id: task.id,
        pdf: task.pdfPath,
        cliArgs: buildCliArgs(task.options),
        env: buildEnv(settings, task.options),
      });
    } catch (e) {
      const h = humanizeError(String(e));
      task.status = "error";
      task.error = h.title;
      task.errorDetail = h.detail;
      this.cleanup(task.id);
      this.emit();
      void this.pump();
    }
  }

  private onEvent(task: QueueTask, ev: PipelineEvent) {
    task.lastEventAt = Date.now();
    task.stalled = false;
    switch (ev.type) {
      case "start":
        task.logs.push(S.convert.logStart(ev.title, ev.engine));
        break;
      case "progress":
        task.percent = ev.percent;
        if (ev.stage != null) task.stage = ev.stage;
        if (ev.stage_name) task.stageName = ev.stage_name;
        if (ev.detail) this.pushLog(task, ev.detail);
        break;
      case "stage_done":
        task.percent = ev.percent;
        this.pushLog(task, S.convert.logStageDone(ev.stage, ev.stage_name, ev.elapsed));
        break;
      case "log":
        this.pushLog(task, ev.line);
        break;
      case "done":
        task.status = "done";
        task.percent = 100;
        task.epubPath = ev.epub_path;
        task.elapsed = ev.elapsed;
        break;
      case "error": {
        // 进程随后以非零退出；这里人话化记录原因
        const h = humanizeError(ev.message);
        task.error = h.title;
        task.errorDetail = h.detail ?? ev.message;
        this.pushLog(task, `✗ ${h.title}`);
        break;
      }
    }
    this.emit();
  }

  private pushLog(task: QueueTask, line: string) {
    task.logs.push(line);
    if (task.logs.length > 500) task.logs.splice(0, task.logs.length - 500);
  }

  private onExit(task: QueueTask, code: number | null) {
    task.exitCode = code;
    if (task.status === "running") {
      // 没收到 done/error 就退出了：按退出码判定
      if (code === 0) {
        task.status = "done";
        task.percent = 100;
      } else if (task.cancelAskedAt) {
        task.status = "cancelled";
      } else {
        task.status = "error";
        if (!task.error) task.error = S.convert.exitCode(code);
      }
    }
    if (task.status === "done") {
      settingsStore.addHistoryDir(task.options.outputDir);
      notify.success(S.convert.toastDone(task.title));
    } else if (task.status === "error") {
      notify.error(`${S.convert.toastError(task.title)}：${task.error ?? ""}`);
    }
    this.cleanup(task.id);
    this.emit();
    void this.pump();
  }

  cancel(id: string) {
    const task = this.tasks.find((t) => t.id === id);
    if (!task) return;
    if (task.status === "queued") {
      task.status = "cancelled";
      this.emit();
      return;
    }
    if (task.status !== "running") return;
    task.cancelAskedAt = Date.now();
    task.status = "cancelled";
    this.emit();
    invoke("cancel_conversion", { id })
      .catch(() => {})
      .finally(() => {
        // 看门狗：6s 后仍未收到退出事件 → 提示可能僵死（只提示，不动作）
        setTimeout(() => {
          if (this.unlisteners.has(id)) {
            notify.warning(S.convert.toastCancelStuck);
          }
        }, CANCEL_WATCHDOG_MS);
      });
  }

  clearFinished() {
    this.tasks = this.tasks.filter(
      (t) => t.status === "queued" || t.status === "running",
    );
    this.emit();
  }

  private checkStall() {
    const now = Date.now();
    let changed = false;
    for (const t of this.tasks) {
      if (t.status === "running" && !t.stalled && now - t.lastEventAt > STALL_MS) {
        t.stalled = true;
        changed = true;
      }
    }
    if (changed) this.emit();
  }

  private cleanup(id: string) {
    const unls = this.unlisteners.get(id);
    if (unls) {
      unls.forEach((u) => u());
      this.unlisteners.delete(id);
    }
  }

  subscribe = (l: Listener) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
  getTasks = () => this.tasks;
  getStarted = () => this.started;
  private emit() {
    this.listeners.forEach((l) => l());
  }
}

export const queueStore = new QueueStore();

export function useQueue(): QueueTask[] {
  return useSyncExternalStore(queueStore.subscribe, queueStore.getTasks);
}

export function useQueueStarted(): boolean {
  return useSyncExternalStore(queueStore.subscribe, queueStore.getStarted);
}
