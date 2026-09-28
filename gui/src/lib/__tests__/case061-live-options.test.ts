// 病例 061：队列选项语义——任务未点火前跟随 settings.defaults，起跑瞬间
// 定稿锁死；「再次转换」（optionsPinned=true）钉死入队快照不跟随。
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

type Listener = (e: { payload: string }) => void;
const listeners = new Map<string, Listener>();
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (name: string, cb: Listener) => {
    listeners.set(name, cb);
    return () => {
      listeners.delete(name);
    };
  }),
}));

import { invoke } from "@tauri-apps/api/core";

const invokeMock = vi.mocked(invoke);

async function freshStores() {
  vi.resetModules();
  const queue = (await import("../queue")).queueStore;
  const settings = (await import("../settings")).settingsStore;
  const { defaultConvertOptions } = await import("../settings");
  return { queue, settings, defaultConvertOptions };
}

async function flush(times = 8) {
  for (let i = 0; i < times; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

function startCalls() {
  return invokeMock.mock.calls.filter((c) => c[0] === "start_conversion");
}

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockResolvedValue(undefined as never);
  listeners.clear();
});

describe("061 队列选项语义", () => {
  it("未钉死任务起跑瞬间取当下 defaults（入队后改开关生效）", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t";
    // 入队时翻译关（与 defaults 一致）
    queue.add(["D:/book.pdf"], defaultConvertOptions());
    const task = queue.tasks[0];
    expect(task.optionsPinned).toBe(false);
    // 入队后才打开翻译 + 目标语言
    settings.settings.defaults.translate = true;
    settings.settings.defaults.translateLang = "zh";

    await queue.startAll();
    await flush();

    expect(task.status).toBe("running");
    const cliArgs = (startCalls()[0][1] as { cliArgs: string[] }).cliArgs;
    expect(cliArgs).toContain("--translate");
    expect(cliArgs).toContain("zh");
    // 起跑瞬间定稿锁死：卡片所见即实际执行选项
    expect(task.options.translate).toBe(true);
    expect(task.stagesTotal).toBe(4);
  });

  it("钉死任务（再次转换）不跟随 defaults 变化", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t";
    queue.add(["D:/book.pdf"], defaultConvertOptions(), true, true);
    const task = queue.tasks[0];
    expect(task.optionsPinned).toBe(true);
    settings.settings.defaults.translate = true;

    await queue.startAll();
    await flush();

    const cliArgs = (startCalls()[0][1] as { cliArgs: string[] }).cliArgs;
    expect(cliArgs).not.toContain("--translate");
    expect(task.options.translate).toBe(false);
    expect(task.stagesTotal).toBe(3);
  });

  it("排队中的后续任务在各自起跑瞬间取当下值（不继承前车的定稿）", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t";
    queue.add(["D:/a.pdf"], defaultConvertOptions());
    queue.add(["D:/b.pdf"], defaultConvertOptions());
    const [a, b] = queue.tasks;
    // 点火前翻译开：A 车应带翻译
    settings.settings.defaults.translate = true;

    await queue.startAll();
    await flush();
    expect(a.status).toBe("running");
    expect(
      (startCalls()[0][1] as { cliArgs: string[] }).cliArgs,
    ).toContain("--translate");

    // A 跑的过程中用户关掉翻译 → B 起跑应按关执行
    settings.settings.defaults.translate = false;
    listeners.get(`conversion::${a.id}::exit`)?.({
      payload: JSON.stringify({ code: 0 }),
    });
    await flush();

    expect(b.status).toBe("running");
    const bArgs = (startCalls()[1][1] as { cliArgs: string[] }).cliArgs;
    expect(bArgs).not.toContain("--translate");
    expect(b.options.translate).toBe(false);
    expect(b.stagesTotal).toBe(3);
  });
});
