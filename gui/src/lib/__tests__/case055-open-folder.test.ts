// 病例 055：done 卡片「打开文件夹」——done 事件 product_dir 透传 +
// epub_path 上溯两级兜底（交付恒为 <work_dir>/epub/<书名>.epub）
import { describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));

async function freshQueue() {
  vi.resetModules();
  const q = await import("../queue");
  const s = await import("../settings");
  return { queueStore: q.queueStore, productDirOf: q.productDirOf, s };
}

describe("done 事件 productDir", () => {
  it("product_dir 透传到 task.productDir", async () => {
    const { queueStore, s } = await freshQueue();
    queueStore.add(["D:/b.pdf"], s.defaultConvertOptions());
    const t = queueStore.tasks[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (queueStore as any).onEvent(t, {
      type: "done",
      epub_path: "D:\\out\\b\\epub\\b.epub",
      title: "b",
      elapsed: 1,
      percent: 100,
      product_dir: "D:\\out\\b",
    });
    expect(t.status).toBe("done");
    expect(t.productDir).toBe("D:\\out\\b");
    expect(t.epubPath).toBe("D:\\out\\b\\epub\\b.epub");
  });

  it("无 product_dir 时从 epub_path 上溯两级兜底", async () => {
    const { queueStore, s } = await freshQueue();
    queueStore.add(["D:/b.pdf"], s.defaultConvertOptions());
    const t = queueStore.tasks[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (queueStore as any).onEvent(t, {
      type: "done",
      epub_path: "D:\\out\\秦汉史讲义（秦晖）\\epub\\秦汉史讲义.epub",
      title: "b",
      elapsed: 1,
      percent: 100,
    });
    expect(t.productDir).toBe("D:\\out\\秦汉史讲义（秦晖）");
  });

  it("productDirOf 纯函数：正/反斜杠通吃，形态不符不动作（铁律 0）", async () => {
    const { productDirOf } = await freshQueue();
    expect(productDirOf("D:/out/b/epub/b.epub")).toBe("D:\\out\\b");
    expect(productDirOf("D:\\out\\b\\epub\\b.epub")).toBe("D:\\out\\b");
    expect(productDirOf("b.epub")).toBeUndefined();
    expect(productDirOf("")).toBeUndefined();
  });

  it("retry 重置时清掉 productDir", async () => {
    const { queueStore, s } = await freshQueue();
    queueStore.add(["D:/b.pdf"], s.defaultConvertOptions());
    const t = queueStore.tasks[0];
    t.status = "error";
    t.productDir = "D:\\out\\b";
    t.epubPath = "D:\\out\\b\\epub\\b.epub";
    queueStore.retry(t.id);
    expect(t.productDir).toBeUndefined();
    expect(t.epubPath).toBeUndefined();
  });
});
