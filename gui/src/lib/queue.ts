// 转换队列：模块级 store（跨页面存活）。拖入只入队为「待开始」，点【开始转换】才开跑；
// 并发上限 1，开跑后自动接力，跑空自动熄火（再次拖入需重新点开始）。
// 选项语义（061 用户裁定）：任务未点火前跟随 settings.defaults，起跑瞬间
// 定稿锁死（pump 写回 task.options）；「再次转换」入队时 optionsPinned=true
// 钉死，重放当时登记选项。
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useSyncExternalStore } from "react";
import { parsePipelineEvent, type PipelineEvent } from "./protocol";
import {
  buildCliArgs,
  buildEnv,
  settingsStore,
  type ConvertOptions,
  type Settings,
} from "./settings";
import { precheckTask } from "./precheck";
import { startSummaryText } from "./start-summary";
import { humanizeError } from "./errors";
import { notify } from "./notify";
import { navigate } from "./nav";
import { S } from "./strings";

export type TaskStatus = "queued" | "running" | "done" | "error" | "cancelled";

/** 从 epub 交付路径上溯产物文件夹：交付恒为 <work_dir>/epub/<书名>.epub，
    去文件名再上溯一级即 work_dir。正反斜杠通吃；形态不符返回 undefined
    （失败方向=不动作，按钮退回旧行为开 epub）。 */
export function productDirOf(epubPath: string): string | undefined {
  const norm = epubPath.replace(/\//g, "\\");
  const parts = norm.split("\\").filter(Boolean);
  if (parts.length < 3) return undefined; // 盘符 + epub + 文件名 至少 3 段
  return parts.slice(0, -2).join("\\");
}

export interface QueueTask {
  id: string;
  pdfPath: string;
  title: string;
  options: ConvertOptions;
  /** true=选项钉死（「再次转换」重放当时的登记选项）；false=未点火前跟随
      settings.defaults——起跑瞬间取当下选项（病例 061 用户裁定：任务未开始，
      开始就按当下设置执行；一旦起跑即锁死） */
  optionsPinned: boolean;
  status: TaskStatus;
  percent: number;
  stage: number | null;
  stageName: string;
  stagesTotal: number;
  /** start 事件携带的阶段边界（累计百分比）；旧 sidecar 无此字段 → null（不画刻度点） */
  stageBounds: number[] | null;
  /** 最新一条 detail（如「VLM 阅读 120/549 页」），卡片常显详情行 */
  detail: string;
  logs: string[];
  error?: string; // 人话标题
  errorDetail?: string; // 原文（技术细节）
  epubPath?: string;
  /** 产物文件夹（<输出目录>/<书名>/，内含 epub/ md/ tex/）：done 事件
      product_dir 透传；无该字段时从 epub_path 上溯两级兜底（交付恒为
      <work_dir>/epub/<书名>.epub） */
  productDir?: string;
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

/** 任务实际应执行的选项：钉死任务用入队快照，其余跟随当下 defaults。
    仅在「预检/汇总/起跑」时点取值；起跑瞬间由 pump 写回 task.options 锁死 */
export function effectiveOptions(
  task: Pick<QueueTask, "options" | "optionsPinned">,
  settings: Settings,
): ConvertOptions {
  return task.optionsPinned ? task.options : settings.defaults;
}

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

  add(paths: string[], options: ConvertOptions, highlight = false, optionsPinned = false) {
    for (const pdfPath of paths) {
      const name = pdfPath.split(/[\\/]/).pop() ?? pdfPath;
      this.tasks.push({
        id: `t${Date.now()}-${seq++}`,
        pdfPath,
        title: name.replace(/\.pdf$/i, ""),
        options: { ...options, formats: [...options.formats] },
        optionsPinned,
        status: "queued",
        percent: 0,
        stage: null,
        stageName: "",
        stagesTotal: options.translate ? 4 : 3,
        stageBounds: null,
        detail: "",
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

  /** 【开始转换】：预检 → 点火 → 接力（输出目录兜底在各任务起跑瞬间） */
  async startAll() {
    const settings = settingsStore.settings;
    for (const task of this.tasks) {
      if (task.status !== "queued") continue;
      // ① 缺 key 预检：卡片置错误态（不依赖一瞬即逝的 toast）+ 人话指明缺
      // 哪个 key + 去设置；该项不再进 pump（旧版 continue 后 hasQueued 仍含
      // 它，会被 pump 以无 key 状态照样起跑——"没反应"的直接来源）。
      // 未钉死任务按当下 defaults 预检（与起跑瞬间取值同口径）
      const check = precheckTask(effectiveOptions(task, settings), settings);
      if (!check.ok) {
        task.status = "error";
        task.error = S.convert.toastMissingKey(check.missing);
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
    }
    if (!this.hasQueued()) {
      this.emit();
      return;
    }
    // ② 起跑前引擎汇总可见（病例 052：入队即快照选项，点火前明示将用哪个引擎；
    // 061 起未钉死任务按当下 defaults 汇总）
    notify.info(
      startSummaryText(
        this.tasks
          .filter((t) => t.status === "queued")
          .map((t) => effectiveOptions(t, settings)),
      ),
    );
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
      // 起跑瞬间定稿：未钉死任务取当下 defaults（061 用户裁定），写回
      // task.options 锁死——此后卡片/重试/日志所见即实际执行选项
      const eff = { ...effectiveOptions(task, settings) };
      // 输出目录兜底：为空则用默认建议值并告知（拖入永不拦截）
      if (!eff.outputDir.trim()) {
        const d = await invoke<string>("default_output_dir").catch(() => "");
        if (d) {
          eff.outputDir = d;
          notify.info(S.convert.toastDefaultDir(d));
        }
      }
      eff.formats = [...eff.formats];
      task.options = eff;
      task.stagesTotal = eff.translate ? 4 : 3;
      this.emit();
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
        if (ev.stage_bounds?.length) {
          // 按预估加权的真实阶段边界：刻度点与阶段总数都以它为准
          task.stageBounds = ev.stage_bounds;
          task.stagesTotal = ev.stage_bounds.length;
        }
        break;
      case "progress":
        task.percent = ev.percent;
        if (ev.stage != null) task.stage = ev.stage;
        if (ev.stage_name) task.stageName = ev.stage_name;
        if (ev.detail) {
          task.detail = ev.detail;
          this.pushLog(task, ev.detail);
        }
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
        task.productDir = ev.product_dir || productDirOf(ev.epub_path);
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

  /**
   * 【重试】失败/已取消任务：就地重置回 queued（不新建卡片），随后走
   * startAll 标准路径——052 预检/引擎汇总照旧生效（预检不过会重新置
   * error，不会带病起跑）。选项按 061 语义：钉死任务保留入队快照，
   * 未钉死任务在起跑瞬间重新取当下 defaults。其他状态一律不动作
   * （铁律 0）。
   */
  retry(id: string) {
    const task = this.tasks.find((t) => t.id === id);
    if (!task) return;
    if (task.status !== "error" && task.status !== "cancelled") return;
    task.status = "queued";
    task.percent = 0;
    task.stage = null;
    task.stageName = "";
    task.stagesTotal = task.options.translate ? 4 : 3;
    task.stageBounds = null;
    task.detail = "";
    task.error = undefined;
    task.errorDetail = undefined;
    task.epubPath = undefined;
    task.productDir = undefined;
    task.elapsed = undefined;
    task.exitCode = undefined;
    task.cancelAskedAt = undefined;
    task.stalled = false;
    task.lastEventAt = Date.now();
    this.pushLog(task, S.convert.logRetry(task.title));
    notify.info(S.convert.toastRetry(task.title));
    this.emit();
    void this.startAll();
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
  // useSyncExternalStore 按 Object.is 比较快照：数组原地 push/改字段引用不变，
  // emit 了 React 也不重渲染（"点了开始没反应"根因）——快照必须在 emit 时
  // 换新引用，且不能在 getSnapshot 里现切（每次调用新引用 = 渲染死循环）
  private snapshot: QueueTask[] = [];
  getTasks = () => this.snapshot;
  getStarted = () => this.started;
  private emit() {
    this.snapshot = this.tasks.slice();
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
