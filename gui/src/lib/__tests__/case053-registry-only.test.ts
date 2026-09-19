// 病例 053：产物库纯登记制——扫描/忽略/重新定位链路移除后的可测逻辑
// ① registryDirsOf 只组装 outputDir + historyDirs（无 scanDirs）
// ② 旧 settings.json 里已持久化的 scanDirs/ignoredPaths 字段加载不炸、静默丢弃
import { describe, expect, it } from "vitest";

import { defaultSettings, migrate } from "../settings";
import { registryDirsOf } from "../registry";

describe("registryDirsOf 纯登记制目录组装", () => {
  it("只剩 outputDir + historyDirs（去重去空）", () => {
    const s = defaultSettings();
    s.defaults.outputDir = "D:/out";
    s.historyDirs = ["D:/books", "D:/out", ""];
    expect(registryDirsOf(s)).toEqual(["D:/out", "D:/books"]);
  });

  it("outputDir 为空时只剩 historyDirs", () => {
    const s = defaultSettings();
    s.historyDirs = ["D:/books"];
    expect(registryDirsOf(s)).toEqual(["D:/books"]);
  });

  it("全空 → 空数组（调用方不发起 read_registry）", () => {
    expect(registryDirsOf(defaultSettings())).toEqual([]);
  });

  it("Settings schema 已无 scanDirs/ignoredPaths 字段", () => {
    const s = defaultSettings() as unknown as Record<string, unknown>;
    expect("scanDirs" in s).toBe(false);
    expect("ignoredPaths" in s).toBe(false);
  });
});

describe("migrate 向后兼容：旧扫描/忽略字段静默丢弃", () => {
  it("含 ignoredPaths/scanDirs 的旧配置加载不炸，字段被剔除", () => {
    const s = migrate({
      version: 2,
      scanDirs: ["D:/old-scan"],
      ignoredPaths: ["D:/a.epub"],
      historyDirs: ["D:/books"],
    } as never);
    expect(s.historyDirs).toEqual(["D:/books"]); // 正常字段不受影响
    const raw = s as unknown as Record<string, unknown>;
    expect("scanDirs" in raw).toBe(false);
    expect("ignoredPaths" in raw).toBe(false);
    // 序列化落盘也不再带旧字段
    const persisted = JSON.parse(JSON.stringify(s)) as Record<string, unknown>;
    expect("scanDirs" in persisted).toBe(false);
    expect("ignoredPaths" in persisted).toBe(false);
  });

  it("无旧字段的配置照常加载（缺失不报错）", () => {
    const s = migrate({ version: 2, sound: false } as never);
    expect(s.sound).toBe(false);
    expect(registryDirsOf(s)).toEqual([]);
  });
});
