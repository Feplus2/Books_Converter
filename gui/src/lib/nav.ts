// 跨模块导航：CustomEvent 总线（挂在 window 上，HMR 重载 lib 模块也不会丢 handler——
// 此前 registerNav 模块级 handler 在 dev 长会话里会被模块重评估重置成 noop，即"假链接"根因）
import type { SettingsSection } from "./precheck";

export type PageId = "convert" | "library" | "manual" | "settings" | "notifications";

export interface NavTarget {
  page: PageId;
  section?: SettingsSection;
  providerId?: string;
}

export const NAV_EVENT = "bc:nav";

export function navigate(t: NavTarget) {
  window.dispatchEvent(new CustomEvent<NavTarget>(NAV_EVENT, { detail: t }));
}

export function onNavigate(h: (t: NavTarget) => void): () => void {
  const listener = (e: Event) => h((e as CustomEvent<NavTarget>).detail);
  window.addEventListener(NAV_EVENT, listener);
  return () => window.removeEventListener(NAV_EVENT, listener);
}
