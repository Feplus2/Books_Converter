// 病例 054：失败/取消任务的「重试」——就地重置回 queued（不新建卡片、
// 选项快照原样保留），走 startAll 标准路径（052 预检照旧生效）
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));

import { invoke } from "@tauri-apps/api/core";

const invokeMock = vi.mocked(invoke);

/** 每次测试拿到全新模块实例（queueStore/settingsStore 是模块级单例，需隔离） */
async function freshStores() {
  vi.resetModules();
  const queue = (await import("../queue")).queueStore;
  const settings = (await import("../settings")).settingsStore;
  const { defaultConvertOptions } = await import("../settings");
  return { queue, settings, defaultConvertOptions };
}

/** 冲刷微任务队列，让 retry() 内 void 掉的 startAll/pump 跑完 */
async function flush(times = 8) {
  for (let i = 0; i < times; i++) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockResolvedValue(undefined as never);
});

describe("queueStore.retry 重试状态流转", () => {
  it("error 任务：重置回 queued 并经 startAll 起跑（预检过 → running）", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t"; // 预检放行
    const options = defaultConvertOptions();
    queue.add(["D:/book.pdf"], options);
    const task = queue.tasks[0];
    // 模拟失败终态
    task.status = "error";
    task.error = "转换失败";
    task.errorDetail = "boom";
    task.percent = 47;
    task.stage = 2;
    task.detail = "阶段细节";
    task.exitCode = 1;

    queue.retry(task.id);
    expect(task.status).toBe("queued");
    // 运行态字段全部复位
    expect(task.percent).toBe(0);
    expect(task.stage).toBeNull();
    expect(task.error).toBeUndefined();
    expect(task.errorDetail).toBeUndefined();
    expect(task.exitCode).toBeUndefined();
    expect(task.detail).toBe("");
    // 不新建卡片、选项快照原样保留
    expect(queue.tasks).toHaveLength(1);
    expect(task.options).toEqual(options);
    expect(task.logs.some((l) => l.includes("重新入队"))).toBe(true);

    await flush();
    expect(task.status).toBe("running");
    expect(invokeMock).toHaveBeenCalledWith(
      "start_conversion",
      expect.objectContaining({ id: task.id, pdf: "D:/book.pdf" }),
    );
  });

  it("cancelled 任务同样可重试", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t";
    queue.add(["D:/book.pdf"], defaultConvertOptions());
    const task = queue.tasks[0];
    task.status = "cancelled";
    queue.retry(task.id);
    expect(task.status).toBe("queued");
    await flush();
    expect(task.status).toBe("running");
  });

  it("052 兼容：预检不过 → 重新置 error 并指明缺 key，不进 pump", async () => {
    const { queue, defaultConvertOptions } = await freshStores();
    // mineruToken 为空 → startAll 预检拦截
    queue.add(["D:/book.pdf"], defaultConvertOptions());
    const task = queue.tasks[0];
    task.status = "error";
    task.error = "旧错误";

    queue.retry(task.id);
    await flush();
    expect(task.status).toBe("error"); // 预检拦截回 error
    expect(task.error).toContain("MinerU");
    expect(
      invokeMock.mock.calls.filter((c) => c[0] === "start_conversion"),
    ).toHaveLength(0);
  });

  it("非终态任务不动作（铁律 0）：queued/running/done 一律 no-op", async () => {
    const { queue, settings, defaultConvertOptions } = await freshStores();
    settings.settings.ocr.mineruToken = "t";
    queue.add(["D:/a.pdf"], defaultConvertOptions());
    queue.add(["D:/b.pdf"], defaultConvertOptions());
    queue.add(["D:/c.pdf"], defaultConvertOptions());
    const [a, b, c] = queue.tasks;
    a.status = "queued";
    b.status = "running";
    c.status = "done";
    for (const t of [a, b, c]) queue.retry(t.id);
    expect(a.status).toBe("queued");
    expect(b.status).toBe("running");
    expect(c.status).toBe("done");
    expect(queue.tasks).toHaveLength(3);
    await flush();
    // retry 全部 no-op，没有任何 startAll 副作用
    expect(
      invokeMock.mock.calls.filter((x) => x[0] === "start_conversion"),
    ).toHaveLength(0);
  });

  it("未知 id 不动作", async () => {
    const { queue } = await freshStores();
    queue.retry("t-nonexistent");
    expect(queue.tasks).toHaveLength(0);
  });
});
