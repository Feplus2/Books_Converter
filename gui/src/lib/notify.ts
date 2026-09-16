// 统一通知入口：sonner toast（去重+限量）+ 通知历史 store（铃铛面板数据源，留最近 100 条）
import { toast } from "sonner";
import { useSyncExternalStore } from "react";

export type NotifyLevel = "success" | "error" | "warning" | "info";

export interface Notice {
  id: string;
  ts: number;
  level: NotifyLevel;
  text: string;
  read: boolean;
}

export interface NotifyAction {
  label: string;
  onClick: () => void;
}

interface NotifyOptions {
  action?: NotifyAction;
  /** true 时只落历史不弹 toast */
  silent?: boolean;
}

type Listener = () => void;

const DEDUPE_MS = 3000;

class NotifyStore {
  notices: Notice[] = [];
  private listeners = new Set<Listener>();
  private recent = new Map<string, number>(); // dedupeKey → last fired ts
  private seq = 0;

  private push(level: NotifyLevel, text: string, opts?: NotifyOptions) {
    const key = `${level}:${text}`;
    const now = Date.now();
    const last = this.recent.get(key);
    const toastId = `n-${key}`;
    if (last && now - last < DEDUPE_MS) {
      // 同内容连发：复用同一 toast id（sonner 原地更新），历史不重复落
      if (!opts?.silent) this.fireToast(level, text, toastId, opts?.action);
      return;
    }
    this.recent.set(key, now);
    this.notices.unshift({
      id: `h-${now}-${this.seq++}`,
      ts: now,
      level,
      text,
      read: false,
    });
    if (this.notices.length > 100) this.notices.length = 100;
    this.emit();
    if (!opts?.silent) this.fireToast(level, text, toastId, opts?.action);
  }

  private fireToast(level: NotifyLevel, text: string, id: string, action?: NotifyAction) {
    toast[level](text, {
      id,
      action: action ? { label: action.label, onClick: action.onClick } : undefined,
    });
  }

  success(text: string, opts?: NotifyOptions) {
    this.push("success", text, opts);
  }
  error(text: string, opts?: NotifyOptions) {
    this.push("error", text, opts);
  }
  warning(text: string, opts?: NotifyOptions) {
    this.push("warning", text, opts);
  }
  info(text: string, opts?: NotifyOptions) {
    this.push("info", text, opts);
  }

  markAllRead() {
    if (this.notices.every((n) => n.read)) return;
    this.notices.forEach((n) => (n.read = true));
    this.emit();
  }

  clear() {
    this.notices = [];
    this.emit();
  }

  remove(id: string) {
    this.notices = this.notices.filter((n) => n.id !== id);
    this.emit();
  }

  unreadCount = () => this.notices.filter((n) => !n.read).length;

  subscribe = (l: Listener) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
  getNotices = () => this.notices;
  private emit() {
    this.listeners.forEach((l) => l());
  }
}

export const notifyStore = new NotifyStore();

export const notify = {
  success: (t: string, o?: NotifyOptions) => notifyStore.success(t, o),
  error: (t: string, o?: NotifyOptions) => notifyStore.error(t, o),
  warning: (t: string, o?: NotifyOptions) => notifyStore.warning(t, o),
  info: (t: string, o?: NotifyOptions) => notifyStore.info(t, o),
};

export function useNotices(): Notice[] {
  return useSyncExternalStore(notifyStore.subscribe, notifyStore.getNotices);
}

export function useUnreadCount(): number {
  return useSyncExternalStore(notifyStore.subscribe, notifyStore.unreadCount);
}
