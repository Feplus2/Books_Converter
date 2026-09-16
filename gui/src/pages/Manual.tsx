import { ArrowRight, BookOpen } from "lucide-react";
import { S } from "../lib/strings";

function Sec({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card mt-4 p-5">
      <h2 className="mb-3 flex items-center gap-2 text-[14px] font-semibold">
        <BookOpen size={15} style={{ color: "var(--accent)" }} />
        {title}
      </h2>
      {children}
    </section>
  );
}

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[14px]">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th
                key={i}
                className="border-b px-3 py-2 text-left font-semibold"
                style={{ borderColor: "var(--line)", color: "var(--ink2)" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td
                  key={j}
                  className="border-b px-3 py-2 align-top"
                  style={{
                    borderColor: "var(--line)",
                    color: j === 0 ? "var(--ink)" : "var(--ink2)",
                    fontWeight: j === 0 ? 500 : 400,
                    // 首列两字词不堆叠
                    ...(j === 0 ? { whiteSpace: "nowrap" as const, minWidth: 64 } : {}),
                  }}
                >
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ManualPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-6 pb-10">
      {/* 这是什么 */}
      <Sec title={S.manual.secWhat}>
        <p className="text-[14px] leading-6" style={{ color: "var(--ink2)" }}>
          {S.manual.whatOneLiner}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {S.manual.whatPipeline.map((step, i) => (
            <span key={step} className="flex items-center gap-2">
              <span
                className="rounded-lg px-3 py-1.5 text-[14px] font-medium"
                style={{
                  background:
                    i === 0
                      ? "color-mix(in srgb, var(--ink) 8%, transparent)"
                      : "color-mix(in srgb, var(--accent) 12%, transparent)",
                  color: i === 0 ? "var(--ink)" : "var(--accent)",
                }}
              >
                {step}
              </span>
              {i < S.manual.whatPipeline.length - 1 && (
                <ArrowRight size={14} style={{ color: "var(--ink2)" }} />
              )}
            </span>
          ))}
        </div>
        <ul className="mt-3 flex flex-col gap-1 text-xs" style={{ color: "var(--ink2)" }}>
          {S.manual.whatStages.map((s, i) => (
            <li key={i}>
              {i + 1}. {s}
            </li>
          ))}
        </ul>
      </Sec>

      {/* 快速开始 */}
      <Sec title={S.manual.secQuick}>
        <div className="flex flex-col gap-3">
          {S.manual.quick.map((q, i) => (
            <div key={i} className="flex gap-3">
              <div
                className="mono flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[14px] font-semibold"
                style={{
                  background: "color-mix(in srgb, var(--accent) 12%, transparent)",
                  color: "var(--accent)",
                }}
              >
                {i + 1}
              </div>
              <div>
                <div className="text-[14px] font-semibold">{q.t}</div>
                <div className="mt-0.5 text-xs leading-5" style={{ color: "var(--ink2)" }}>
                  {q.d}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Sec>

      {/* 两种模式怎么选 */}
      <Sec title={S.manual.secModes}>
        <Table head={S.manual.modeHead} rows={S.manual.modeRows} />
        <div className="mt-2 text-xs leading-5" style={{ color: "var(--warn)" }}>
          {S.manual.modesHint}
        </div>
      </Sec>

      {/* 选项说明 */}
      <Sec title={S.manual.secOptions}>
        <div className="flex flex-col gap-3">
          {S.manual.options.map(([k, v]) => (
            <div key={k}>
              <div className="text-[14px] font-semibold">{k}</div>
              <div className="mt-0.5 text-xs leading-5" style={{ color: "var(--ink2)" }}>
                {v}
              </div>
            </div>
          ))}
        </div>
      </Sec>

      {/* FAQ */}
      <Sec title={S.manual.secFaq}>
        <div className="flex flex-col gap-3">
          {S.manual.faqs.map(([q, a]) => (
            <div key={q} className="card p-3" style={{ background: "var(--card2)" }}>
              <div className="text-[14px] font-semibold">{q}</div>
              <div className="mt-1 text-xs leading-5" style={{ color: "var(--ink2)" }}>
                {a}
              </div>
            </div>
          ))}
        </div>
      </Sec>
    </div>
  );
}
