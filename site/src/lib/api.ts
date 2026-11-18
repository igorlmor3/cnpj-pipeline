import { parseTrace } from "./trace";
import type { AgentExample } from "./types";

export const MAX_QUESTION_CHARS = 300;

export type AskOutcome =
  | { kind: "answered"; answer: AgentExample }
  | { kind: "refused"; message: string }
  | { kind: "paused"; message: string }
  | { kind: "limited"; message: string; retryAfterSeconds: number | null }
  | { kind: "error"; message: string };

interface ApiBody {
  status?: unknown;
  message?: unknown;
  sql?: unknown;
  columns?: unknown;
  rows?: unknown;
  truncated?: unknown;
  trace?: unknown;
}

const GENERIC = "Não foi possível obter uma resposta agora. Tente de novo em instantes.";

const text = (value: unknown): string => (typeof value === "string" ? value : "");

function asRows(value: unknown): (string | number | null)[][] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(Array.isArray)
    .map((row) =>
      (row as unknown[]).map((cell) => (typeof cell === "number" || typeof cell === "string" ? cell : null)),
    );
}

/** Traduz a resposta da API (status HTTP e corpo) no que a pagina precisa mostrar. */
export function interpret(httpStatus: number, body: ApiBody | null, question: string): AskOutcome {
  const status = text(body?.status);
  const message = text(body?.message);

  if (httpStatus === 200 && status === "answered" && message) {
    return {
      kind: "answered",
      answer: {
        id: "ao-vivo",
        pergunta: question,
        status,
        resposta: message,
        sql: text(body?.sql) || undefined,
        colunas: Array.isArray(body?.columns) ? (body?.columns as unknown[]).map(String) : undefined,
        linhas: asRows(body?.rows),
        truncado: body?.truncated === true,
        trace: parseTrace(body?.trace, "en"),
      },
    };
  }
  if (httpStatus === 200 && (status === "out_of_scope" || status === "failed")) {
    return { kind: "refused", message: message || GENERIC };
  }
  if (httpStatus === 503 && (status === "disabled" || status === "unavailable" || status === "budget")) {
    return { kind: "paused", message: message || GENERIC };
  }
  if (httpStatus === 429) {
    return { kind: "limited", message: message || GENERIC, retryAfterSeconds: null };
  }
  if (httpStatus === 400 && message) return { kind: "refused", message };
  return { kind: "error", message: GENERIC };
}

export async function askAgent(
  apiUrl: string,
  question: string,
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<AskOutcome> {
  const trimmed = question.trim();
  if (!trimmed) return { kind: "refused", message: "Escreva uma pergunta sobre o cadastro de CNPJ." };
  if (trimmed.length > MAX_QUESTION_CHARS) {
    return { kind: "refused", message: `Use até ${MAX_QUESTION_CHARS} caracteres na pergunta.` };
  }
  try {
    const response = await fetchImpl(`${apiUrl.replace(/\/+$/, "")}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: trimmed }),
      signal,
    });
    let body: ApiBody | null = null;
    try {
      body = (await response.json()) as ApiBody;
    } catch {
      body = null;
    }
    return interpret(response.status, body, trimmed);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: "error", message: GENERIC };
  }
}
