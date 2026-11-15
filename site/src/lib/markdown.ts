/**
 * Leitor minimo de markdown para o texto do agente: paragrafos, tabelas com barras e blocos de codigo.
 * Devolve uma estrutura de dados, nunca HTML: quem renderiza escreve texto, nao markup, e por isso
 * nada que venha da API pode virar elemento da pagina.
 */
export type Block =
  | { type: "paragraph"; text: string }
  | { type: "table"; head: string[]; rows: string[][] }
  | { type: "code"; text: string };

const isRow = (line: string): boolean => line.includes("|") && line.trim().length > 1;
const isSeparator = (line: string): boolean => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

function cells(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.trim());
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) blocks.push({ type: "paragraph", text: paragraph.join(" ").trim() });
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
    if (line.trim().startsWith("```")) {
      flush();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith("```")) {
        code.push(lines[i] ?? "");
        i += 1;
      }
      blocks.push({ type: "code", text: code.join("\n") });
      continue;
    }
    const next = lines[i + 1];
    if (isRow(line) && next !== undefined && isSeparator(next)) {
      flush();
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && isRow(lines[i] ?? "") && (lines[i] ?? "").trim() !== "") {
        const row = cells(lines[i] ?? "");
        while (row.length < head.length) row.push("");
        rows.push(row.slice(0, head.length));
        i += 1;
      }
      i -= 1;
      blocks.push({ type: "table", head, rows });
      continue;
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

/** Divide um texto em trechos normais e em negrito (**texto**), sem interpretar mais nada. */
export function inlineParts(text: string): { text: string; bold: boolean }[] {
  const parts: { text: string; bold: boolean }[] = [];
  const pattern = /\*\*([^*]+)\*\*/g;
  let last = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > last) parts.push({ text: text.slice(last, match.index), bold: false });
    parts.push({ text: match[1] ?? "", bold: true });
    last = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (last < text.length) parts.push({ text: text.slice(last), bold: false });
  return parts;
}
