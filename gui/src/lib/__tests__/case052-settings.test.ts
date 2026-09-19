// 病例 052：多实例配置分叉 / 产物库去污 / startAll 汇总 的可测逻辑
// （扫描/忽略名单已于病例 053 移除，相关用例由 case053-registry-only 接管）
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import {
  defaultConvertOptions,
  isRepoInternalDir,
  pruneRepoInternalDirs,
} from "../settings";
import { startSummaryText } from "../start-summary";

const invokeMock = vi.mocked(invoke);

/** 每次测试拿到全新模块实例（settingsStore 是模块级单例，需隔离） */
async function freshStore() {
  vi.resetModules();
  const mod = await import("../settings");
  return mod.settingsStore;
}

beforeEach(() => {
  invokeMock.mockReset();
});

describe("isRepoInternalDir 仓库内暂存区判定", () => {
  it("命中 _regress / output / .tmp-*（正反斜杠、尾斜杠、大小写不敏感）", () => {
    expect(isRepoInternalDir("F:\\MyProjects\\Books_Converter\\_regress\\smoke-out")).toBe(true);
    expect(isRepoInternalDir("F:/MyProjects/Books_Converter/output")).toBe(true);
    expect(isRepoInternalDir("f:/myprojects/books_converter/.tmp-epub-zh/")).toBe(true);
    expect(isRepoInternalDir("F:/MyProjects/Books_Converter/_regress")).toBe(true);
  });

  it("不命中：通用目录、相似名目录、仓库根本身（失败方向=不误伤）", () => {
    expect(isRepoInternalDir("D:\\temp_files")).toBe(false);
    expect(isRepoInternalDir("D:\\books_converter_notes\\_regress")).toBe(false);
    expect(isRepoInternalDir("D:\\_regress")).toBe(false);
    expect(isRepoInternalDir("F:/MyProjects/Books_Converter")).toBe(false);
    expect(isRepoInternalDir("")).toBe(false);
  });
});

describe("pruneRepoInternalDirs", () => {
  it("剔除仓库内与空串，保留正常目录", () => {
    expect(
      pruneRepoInternalDirs([
        "F:/MyProjects/Books_Converter/_regress/smoke-out",
        "D:/books",
        "",
        "F:/MyProjects/Books_Converter/output/x",
      ]),
    ).toEqual(["D:/books"]);
  });
});

describe("startSummaryText 起跑前引擎汇总", () => {
  const opts = (patch: Partial<ReturnType<typeof defaultConvertOptions>>) => ({
    ...defaultConvertOptions(),
    ...patch,
  });

  it("混合引擎按首见顺序聚合", () => {
    const text = startSummaryText([
      opts({ mode: "rule", ruleEngine: "mineru" }),
      opts({ mode: "vlm" }),
    ]);
    expect(text).toBe("即将开始 2 个任务：MinerU×1、VLM×1");
  });

  it("同引擎合并计数", () => {
    expect(startSummaryText([opts({}), opts({})])).toBe("即将开始 2 个任务：MinerU×2");
  });

  it("单任务与 paddleocr 显示名", () => {
    expect(startSummaryText([opts({ mode: "vlm" })])).toBe("即将开始 1 个任务：VLM×1");
    expect(startSummaryText([opts({ ruleEngine: "paddleocr" })])).toBe(
      "即将开始 1 个任务：PaddleOCR×1",
    );
  });
});

describe("SettingsStore 病例 052 行为", () => {
  const fileWith = (patch: object) =>
    JSON.stringify({ version: 2, ...patch });

  it("load：historyDirs 剔除仓库内 + 已不存在目录（existing_dirs 裁定存在性）", async () => {
    invokeMock.mockImplementation(async (cmd, args) => {
      if (cmd === "read_settings_state") {
        return {
          mtime_ms: 100,
          content: fileWith({
            historyDirs: [
              "F:/MyProjects/Books_Converter/_regress/smoke-out",
              "D:/exists",
              "D:/gone",
            ],
          }),
        };
      }
      if (cmd === "existing_dirs") {
        return (args as { dirs: string[] }).dirs.filter((d) => d === "D:/exists");
      }
      throw new Error(`unexpected invoke: ${String(cmd)}`);
    });
    const store = await freshStore();
    await store.load();
    expect(store.settings.historyDirs).toEqual(["D:/exists"]);
  });

  it("load：dev 文件不存在 → 退回 load_settings（debug 一次性继承正式配置）", async () => {
    invokeMock.mockImplementation(async (cmd) => {
      if (cmd === "read_settings_state") return { mtime_ms: null, content: null };
      if (cmd === "load_settings") return fileWith({ sound: false });
      if (cmd === "existing_dirs") return [];
      throw new Error(`unexpected invoke: ${String(cmd)}`);
    });
    const store = await freshStore();
    await store.load();
    expect(store.settings.sound).toBe(false);
  });

  it("聚焦重载：mtime 相同 → 不动；内容一致 → 不动；更新内容 → 重载；坏 JSON/读失败 → 保持内存", async () => {
    let state = { mtime_ms: 100 as number | null, content: fileWith({ sound: true }) as string | null };
    invokeMock.mockImplementation(async (cmd, args) => {
      if (cmd === "read_settings_state") return state;
      if (cmd === "existing_dirs") return (args as { dirs: string[] }).dirs;
      if (cmd === "save_settings") return null;
      throw new Error(`unexpected invoke: ${String(cmd)}`);
    });
    const store = await freshStore();
    await store.load();
    expect(store.settings.sound).toBe(true);

    // ① mtime 相同（即便内容被改）→ 不重载
    state = { mtime_ms: 100, content: fileWith({ sound: false }) };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(true);

    // ② mtime 更新但内容一致 → 不重载（自己保存后的 mtime 漂移）
    state = { mtime_ms: 200, content: fileWith({ sound: true }) };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(true);

    // ③ 另一实例写入了新内容 → 重载
    state = { mtime_ms: 300, content: fileWith({ sound: false }) };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(false);

    // ④ 坏 JSON → 保持内存
    state = { mtime_ms: 400, content: "{broken" };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(false);

    // ⑤ 文件消失 → 保持内存
    state = { mtime_ms: null, content: null };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(false);
  });

  it("聚焦重载：自己刚保存的 mtime（≤lastWriteAt）不触发重载", async () => {
    let state = { mtime_ms: 100 as number | null, content: fileWith({ sound: true }) as string | null };
    invokeMock.mockImplementation(async (cmd, args) => {
      if (cmd === "read_settings_state") return state;
      if (cmd === "existing_dirs") return (args as { dirs: string[] }).dirs;
      if (cmd === "save_settings") return null;
      throw new Error(`unexpected invoke: ${String(cmd)}`);
    });
    const store = await freshStore();
    await store.load();
    store.update({ sound: false }); // 自己保存
    // 磁盘文件是自己刚写的：mtime 现在（≤lastWriteAt），内容是保存后的内容
    state = { mtime_ms: Date.now(), content: JSON.stringify(store.settings, null, 2) };
    await store.reloadIfChanged();
    expect(store.settings.sound).toBe(false);
  });

  it("addHistoryDir：仓库内目录不入历史；正常目录置顶去重", async () => {
    invokeMock.mockImplementation(async (cmd, args) => {
      if (cmd === "read_settings_state") return { mtime_ms: 1, content: fileWith({}) };
      if (cmd === "existing_dirs") return (args as { dirs: string[] }).dirs;
      if (cmd === "save_settings") return null;
      throw new Error(`unexpected invoke: ${String(cmd)}`);
    });
    const store = await freshStore();
    await store.load();
    store.addHistoryDir("F:\\MyProjects\\Books_Converter\\_regress\\smoke-out");
    expect(store.settings.historyDirs).toEqual([]);
    store.addHistoryDir("D:/books");
    store.addHistoryDir("D:/other");
    store.addHistoryDir("D:/books");
    expect(store.settings.historyDirs).toEqual(["D:/books", "D:/other"]);
  });
});
