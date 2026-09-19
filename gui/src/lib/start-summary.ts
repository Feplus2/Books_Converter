// startAll 起跑前的引擎汇总（病例 052：入队即快照选项，起跑前让用户看见将用哪个引擎）。
// 纯函数抽出以便 vitest 覆盖。
import { engineOf, type ConvertOptions } from "./settings";
import { engineName, S } from "./strings";

/** 按引擎聚合 queued 任务选项，产出「即将开始 N 个任务：MinerU×1、VLM×1」 */
export function startSummaryText(optionsList: ConvertOptions[]): string {
  const counts = new Map<string, number>();
  for (const o of optionsList) {
    const e = engineOf(o);
    counts.set(e, (counts.get(e) ?? 0) + 1);
  }
  const parts = [...counts].map(([e, n]) => `${engineName(e)}×${n}`).join("、");
  return S.convert.toastStartSummary(optionsList.length, parts);
}
