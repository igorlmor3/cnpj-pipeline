import { inlineParts, parseMarkdown } from "../lib/markdown";
import type { AgentExample } from "../lib/types";
import { DataTable } from "./ui";

function Inline({ text }: { text: string }) {
  return (
    <>
      {inlineParts(text).map((part, i) =>
        part.bold ? <strong key={`${i}-${part.text}`}>{part.text}</strong> : <span key={`${i}-${part.text}`}>{part.text}</span>,
      )}
    </>
  );
}

/** Texto do agente: paragrafos, listas, tabelas e codigo, sempre como texto (nunca como HTML). */
export function AnswerText({ text }: { text: string }) {
  const blocks = parseMarkdown(text);
  return (
    <div className="answer">
      {blocks.map((block, i) => {
        if (block.type === "paragraph") {
          return (
            <p key={`p-${i}`}>
              <Inline text={block.text} />
            </p>
          );
        }
        if (block.type === "list") {
          const items = block.items.map((item, j) => (
            <li key={`l-${i}-${j}`}>
              <Inline text={item} />
            </li>
          ));
          return block.ordered ? (
            <ol key={`o-${i}`} start={block.start}>
              {items}
            </ol>
          ) : (
            <ul key={`u-${i}`}>{items}</ul>
          );
        }
        if (block.type === "code") {
          return (
            <pre key={`c-${i}`}>
              <code>{block.text}</code>
            </pre>
          );
        }
        return <DataTable key={`t-${i}`} label="Tabela na resposta" spec={{ head: block.head, rows: block.rows }} />;
      })}
    </div>
  );
}

export function AnswerView({ answer }: { answer: AgentExample }) {
  const rows = answer.linhas ?? [];
  const columns = answer.colunas ?? [];
  return (
    <div>
      <AnswerText text={answer.resposta} />
      {answer.sql ? (
        <details style={{ marginTop: "0.8rem" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Consulta SQL executada</summary>
          <pre className="sql" style={{ marginTop: "0.6rem" }}>
            <code>{answer.sql}</code>
          </pre>
        </details>
      ) : null}
      {columns.length > 0 && rows.length > 0 ? (
        <details style={{ marginTop: "0.8rem" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Resultado da consulta ({rows.length} linhas)</summary>
          <div style={{ marginTop: "0.6rem" }}>
            <DataTable
              label="Resultado da consulta"
              spec={{ head: columns, rows: rows.map((row) => row.map((cell) => (cell === null ? "" : cell))) }}
            />
            {answer.truncado ? (
              <p className="poster__foot">O resultado foi limitado e pode ter mais linhas do que as mostradas.</p>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}
