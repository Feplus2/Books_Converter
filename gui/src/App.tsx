import { useEffect, useState } from "react";
import { Toaster } from "sonner";
import {
  ArrowRightLeft,
  Bell,
  BookOpen,
  ChevronsLeft,
  ChevronsRight,
  LibraryBig,
  Settings2,
} from "lucide-react";
import { settingsStore, useSettings } from "./lib/settings";
import { onNavigate, type PageId } from "./lib/nav";
import type { SettingsSection } from "./lib/precheck";
import { useUnreadCount } from "./lib/notify";
import { S } from "./lib/strings";
import { Tooltip } from "./components/Tooltip";
import { ConvertPage } from "./pages/Convert";
import { LibraryPage } from "./pages/Library";
import { ManualPage } from "./pages/Manual";
import { NotificationsPage } from "./pages/Notifications";
import { SettingsPage } from "./pages/Settings";

function applyTheme(theme: "light" | "dark" | "system") {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  return dark ? "dark" : "light";
}

/** 全部导航项统一结构：同尺寸图标、同行高、collapsed 态水平居中（含铃铛的未读徽标） */
function NavButton({
  icon: Icon,
  label,
  active,
  collapsed,
  badge,
  onClick,
}: {
  icon: typeof LibraryBig;
  label: string;
  active: boolean;
  collapsed: boolean;
  badge?: number;
  onClick: () => void;
}) {
  const btn = (
    <button
      className="relative flex w-full cursor-pointer items-center gap-3 rounded-[10px] text-[14px] font-medium transition-all duration-150"
      style={{
        justifyContent: collapsed ? "center" : "flex-start",
        padding: collapsed ? "11px 0" : "9px 12px",
        background: active ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "transparent",
        color: active ? "var(--accent)" : "var(--ink2)",
        boxShadow: active ? "0 0 14px color-mix(in srgb, var(--accent) 14%, transparent)" : undefined,
      }}
      onClick={onClick}
    >
      <span className="relative inline-flex shrink-0">
        <Icon size={17} />
        {!!badge && collapsed && (
          <span
            className="absolute flex items-center justify-center rounded-full font-semibold text-white"
            style={{
              minWidth: 14,
              height: 14,
              padding: "0 3px",
              fontSize: 9,
              background: "var(--err)",
              top: -6,
              right: -8,
            }}
          >
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </span>
      {!collapsed && label}
      {!!badge && !collapsed && (
        <span
          className="ml-auto flex items-center justify-center rounded-full font-semibold text-white"
          style={{
            minWidth: 18,
            height: 18,
            padding: "0 4px",
            fontSize: 10,
            background: "var(--err)",
          }}
        >
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </button>
  );
  return collapsed ? (
    <Tooltip label={label} side="right">
      {btn}
    </Tooltip>
  ) : (
    btn
  );
}

export default function App() {
  const settings = useSettings();
  const unread = useUnreadCount();
  const [page, setPage] = useState<PageId>("convert");
  const [settingsTarget, setSettingsTarget] = useState<{
    section?: SettingsSection;
    providerId?: string;
  }>({});
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    void settingsStore.load();
    return onNavigate((t) => {
      setPage(t.page);
      if (t.page === "settings") {
        setSettingsTarget({ section: t.section, providerId: t.providerId });
      }
    });
  }, []);

  // 主题
  useEffect(() => {
    setTheme(applyTheme(settings.appearance.theme));
    if (settings.appearance.theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setTheme(applyTheme("system"));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [settings.appearance.theme]);

  // 缩放：纯前端 CSS zoom（绕开 Tauri API 与 WebView2 原生加速键），启动恢复 + 变更即应用
  useEffect(() => {
    document.documentElement.style.zoom = String(settings.appearance.zoom);
  }, [settings.appearance.zoom]);

  // Ctrl/Cmd + =/+/−/0（含小键盘）缩放，Ctrl+滚轮缩放；* 也归位 100%
  useEffect(() => {
    const apply = (next: number) => {
      const s = settingsStore.settings;
      const zoom = Math.min(1.5, Math.max(0.7, Math.round(next * 10) / 10));
      if (zoom === s.appearance.zoom) return;
      settingsStore.update({ appearance: { ...s.appearance, zoom } });
    };
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const z = settingsStore.settings.appearance.zoom;
      if (e.key === "=" || e.key === "+" || e.code === "NumpadAdd") {
        e.preventDefault();
        apply(z + 0.1);
      } else if (e.key === "-" || e.key === "_" || e.code === "NumpadSubtract") {
        e.preventDefault();
        apply(z - 0.1);
      } else if (e.key === "0" || e.code === "Numpad0" || e.key === "*") {
        e.preventDefault();
        apply(1);
      }
    };
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const z = settingsStore.settings.appearance.zoom;
      apply(z + (e.deltaY < 0 ? 0.1 : -0.1));
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("wheel", onWheel);
    };
  }, []);

  const go = (p: PageId) => {
    if (p === "settings") setSettingsTarget({});
    setPage(p);
  };

  return (
    <div className="flex h-full">
      {/* 左侧导航栏 */}
      <nav
        className="flex shrink-0 flex-col gap-1 border-r p-3 transition-[width] duration-150"
        style={{
          width: collapsed ? 60 : 176,
          borderColor: "var(--line)",
          background: "var(--card)",
        }}
      >
        <Tooltip label={collapsed ? S.nav.expand : S.nav.collapse} side="right">
          <button
            className="icon-btn mb-1 self-center"
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
          </button>
        </Tooltip>
        <NavButton
          icon={ArrowRightLeft}
          label={S.nav.convert}
          active={page === "convert"}
          collapsed={collapsed}
          onClick={() => go("convert")}
        />
        <NavButton
          icon={LibraryBig}
          label={S.nav.library}
          active={page === "library"}
          collapsed={collapsed}
          onClick={() => go("library")}
        />
        <div className="flex-1" />
        <NavButton
          icon={BookOpen}
          label={S.nav.manual}
          active={page === "manual"}
          collapsed={collapsed}
          onClick={() => go("manual")}
        />
        <NavButton
          icon={Settings2}
          label={S.nav.settings}
          active={page === "settings"}
          collapsed={collapsed}
          onClick={() => go("settings")}
        />
        <NavButton
          icon={Bell}
          label={S.nav.notifications}
          active={page === "notifications"}
          collapsed={collapsed}
          badge={unread || undefined}
          onClick={() => go("notifications")}
        />
      </nav>

      {/* 主区域 */}
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div key={page} className="page-enter">
          {page === "convert" && <ConvertPage />}
          {page === "library" && <LibraryPage />}
          {page === "manual" && <ManualPage />}
          {page === "notifications" && <NotificationsPage />}
          {page === "settings" && (
            <SettingsPage
              section={settingsTarget.section}
              providerId={settingsTarget.providerId}
            />
          )}
        </div>
      </main>

      <Toaster position="top-right" richColors theme={theme} gap={8} visibleToasts={3} />
    </div>
  );
}
