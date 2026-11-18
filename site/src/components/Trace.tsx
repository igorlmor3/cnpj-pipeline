import type { AgentTrace } from "../lib/types";

const int = (value: number): string => value.toLocaleString("pt-BR");

function bytesLabel(bytes: number): string {
  if (bytes < 1024) return `${int(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} KiB`;
  return `${(bytes / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MiB`;
}

const plural = (n: number, one: string, many: string): string => `${int(n)} ${n === 1 ? one : many}`;

function fidelityText(trace: AgentTrace): string | null {
  const f = trace.fidelity;
  if (!f) return null;
  if (f.faithful === null || f.checked === 0) return "O texto da resposta não tem números para conferir.";
  if (f.faithful) {
    return `${plural(f.checked, "número do texto foi conferido", "números do texto foram conferidos")} e todos aparecem no resultado da consulta, na pergunta, no SQL ou nos trechos lidos.`;
  }
  return `${plural(f.checked, "número do texto foi conferido", "números do texto foram conferidos")}, e ${plural(f.unsupported.length, "não tem apoio", "não têm apoio")} em nenhuma dessas fontes: ${f.unsupported.join(", ")}.`;
}

/**
 * Como a resposta foi montada: consultas, buscas na documentacao, uso do modelo e conferencia dos numeros.
 * O programa registra isso enquanto executa; a ordem exata entre consultas e buscas nao e guardada, por isso
 * os passos aparecem agrupados por tipo e nao como uma linha do tempo.
 */
export function TraceView({ trace }: { trace: AgentTrace }) {
  const noWork = trace.queries.length === 0 && trace.docSearches.length === 0;
  const fidelity = fidelityText(trace);
  return (
    <details className="trace" style={{ marginTop: "0.8rem" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Como chegou lá</summary>
      <p className="trace__lead">O programa registra o que executou enquanto o agente trabalha. O modelo não escreve nada desta lista.</p>
      <ol className="trace__steps">
        {noWork ? (
          <li>
            <strong>Nenhuma consulta nem busca</strong>
            <span>O agente não executou SQL nem buscou na documentação. A pergunta foi recusada com o que o modelo já tinha no prompt.</span>
          </li>
        ) : null}
        {trace.queries.map((q, i) => (
          <li key={`q-${i}`}>
            <strong>
              Consulta SQL {i + 1}: {q.ok ? "executada" : "não executada"}
            </strong>
            {q.ok ? (
              <span>
                {q.rows !== null ? plural(q.rows, "linha", "linhas") : "Sem contagem de linhas"}
                {q.cut ? ", resultado cortado pelo limite" : ", resultado completo"}
                {q.bytesScanned !== null ? `, ${bytesLabel(q.bytesScanned)} lidos no Athena` : ""}.
              </span>
            ) : (
              <span>{q.error ?? "A consulta falhou."} O agente pôde corrigir e tentar de novo.</span>
            )}
            {q.sql ? (
              <details>
                <summary style={{ cursor: "pointer" }}>SQL desta consulta</summary>
                <pre className="sql">
                  <code>{q.sql}</code>
                </pre>
              </details>
            ) : null}
          </li>
        ))}
        {trace.docSearches.map((d, i) => (
          <li key={`d-${i}`}>
            <strong>Busca na documentação {i + 1}</strong>
            <span>
              Consulta: {d.query || "(vazia)"}.{" "}
              {d.sources.length > 0 ? `Fontes: ${d.sources.join("; ")}.` : "Nenhum trecho encontrado."}
            </span>
          </li>
        ))}
        <li>
          <strong>Modelo</strong>
          <span>
            {plural(trace.llmCalls, "chamada", "chamadas")}, {int(trace.inputTokens)} tokens de entrada e {int(trace.outputTokens)} de saída, custo de US${" "}
            {trace.costUsd.toLocaleString("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}.
          </span>
        </li>
        {fidelity ? (
          <li>
            <strong>Conferência dos números</strong>
            <span>{fidelity}</span>
          </li>
        ) : null}
      </ol>
    </details>
  );
}
