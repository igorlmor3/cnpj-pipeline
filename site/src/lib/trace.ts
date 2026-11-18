import type { AgentTrace, TraceDocSearch, TraceFidelity, TraceQuery } from "./types";

/** Nomes dos campos nos dois formatos de agent/trace.py: a API fala em ingles, os exemplos gravados em portugues. */
const KEYS = {
  en: {
    queries: "queries", sql: "sql", ok: "ok", error: "error", rows: "rows", cut: "cut", bytes: "bytes_scanned",
    docs: "doc_searches", query: "query", sources: "sources", llm: "llm_calls", tin: "input_tokens", tout: "output_tokens",
    cost: "cost_usd", fidelity: "fidelity", checked: "numbers_checked", unsupported: "unsupported", faithful: "faithful",
  },
  pt: {
    queries: "consultas", sql: "sql", ok: "ok", error: "erro", rows: "linhas", cut: "cortado", bytes: "bytes_lidos",
    docs: "buscas_docs", query: "consulta", sources: "fontes", llm: "chamadas_modelo", tin: "tokens_entrada", tout: "tokens_saida",
    cost: "custo_usd", fidelity: "fidelidade", checked: "numeros_verificados", unsupported: "sem_apoio", faithful: "fiel",
  },
} as const;

type Raw = Record<string, unknown>;
const isObject = (value: unknown): value is Raw => typeof value === "object" && value !== null && !Array.isArray(value);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []);

/** Le o trajeto vindo da API ou do JSON dos exemplos. O que nao tiver o formato esperado e ignorado, nunca vira erro. */
export function parseTrace(raw: unknown, lang: "en" | "pt"): AgentTrace | null {
  if (!isObject(raw)) return null;
  const k = KEYS[lang];
  const queries: TraceQuery[] = (Array.isArray(raw[k.queries]) ? (raw[k.queries] as unknown[]) : []).filter(isObject).map((q) => ({
    sql: typeof q[k.sql] === "string" ? (q[k.sql] as string) : "",
    ok: q[k.ok] === true,
    error: typeof q[k.error] === "string" && q[k.error] !== "" ? (q[k.error] as string) : null,
    rows: num(q[k.rows]),
    cut: typeof q[k.cut] === "boolean" ? (q[k.cut] as boolean) : null,
    bytesScanned: num(q[k.bytes]),
  }));
  const docSearches: TraceDocSearch[] = (Array.isArray(raw[k.docs]) ? (raw[k.docs] as unknown[]) : []).filter(isObject).map((d) => ({
    query: typeof d[k.query] === "string" ? (d[k.query] as string) : "",
    ok: d[k.ok] === true,
    sources: strings(d[k.sources]),
  }));
  let fidelity: TraceFidelity | null = null;
  const f = raw[k.fidelity];
  if (isObject(f)) {
    fidelity = {
      checked: num(f[k.checked]) ?? 0,
      faithful: typeof f[k.faithful] === "boolean" ? (f[k.faithful] as boolean) : null,
      unsupported: strings(f[k.unsupported]),
    };
  }
  return {
    queries,
    docSearches,
    llmCalls: num(raw[k.llm]) ?? 0,
    inputTokens: num(raw[k.tin]) ?? 0,
    outputTokens: num(raw[k.tout]) ?? 0,
    costUsd: num(raw[k.cost]) ?? 0,
    fidelity,
  };
}
