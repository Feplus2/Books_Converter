// 报错统一人话化映射层（纯函数，vitest 覆盖）
import { S } from "./strings";

export interface HumanError {
  title: string;
  detail?: string; // 原文，收进「技术细节」
}

export function humanizeError(raw: string): HumanError {
  const text = (raw || "").trim();
  if (!text) return { title: S.err.unknown };
  const first = text.split("\n").map((l) => l.trim()).find(Boolean) ?? text;
  const hay = text.toLowerCase();

  let title: string | null = null;
  if (/\b(401|403)\b|unauthorized|forbidden|invalid[_ ]api[_ ]key|incorrect api key|authentication/.test(hay)) {
    title = S.err.invalidKey;
  } else if (/\b429\b|rate.?limit|too many requests|限流/.test(hay)) {
    title = S.err.rateLimited;
  } else if (/timed? ?out|超时|econn\w*|enetunreach|网络连接|connection (refused|reset|error)|connect fail/.test(hay)) {
    title = S.err.network;
  }
  if (!title) title = first.length > 60 ? `${first.slice(0, 60)}…` : first;
  // 单行短报错不必重复细节
  return { title, detail: text === title ? undefined : text };
}
