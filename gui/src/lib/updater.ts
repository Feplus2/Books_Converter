// 检查更新结果 → 设置页内联消息（病例 058 纯函数，vitest 断言）
// 失败方向=如实提示+给手动出口：任何状态都带 Releases 页链接（url 缺失/
// 检查失败时回退 releases 列表页——API 不通时用户仍可手动去看）。
import { S } from "./strings";

/** GitHub Releases 发布页（updater.py RELEASES_URL 同值；前端兜底常量） */
export const RELEASES_URL = "https://github.com/Feplus2/Books_Converter/releases";

export interface UpdateCheckResult {
  status: string;
  latest?: string;
  url?: string;
  error?: string;
}

export interface UpdateMsg {
  tone: "ok" | "warn" | "err";
  text: string;
  /** Releases 页链接：三态恒有（成功=直达该 tag 页；其余=列表页） */
  url: string;
}

export function updateResultToMsg(r: UpdateCheckResult): UpdateMsg {
  if (r.status === "latest") {
    return { tone: "ok", text: S.settings.updateLatest, url: RELEASES_URL };
  }
  if (r.status === "update") {
    return {
      tone: "warn",
      text: S.settings.updateFound(r.latest ?? ""),
      url: r.url || RELEASES_URL,
    };
  }
  return {
    tone: "err",
    text: S.settings.updateFailed(r.error ?? ""),
    url: RELEASES_URL,
  };
}
