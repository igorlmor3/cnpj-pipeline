import { describe, expect, it } from "vitest";
import { askAgent, interpret } from "../src/lib/api";
import { bytes, compact, int, monthLabel, pct, seconds, titleCase, ufLabel } from "../src/lib/format";
import { inlineParts, parseMarkdown } from "../src/lib/markdown";
import { evenTicks, niceStep, niceTicks } from "../src/lib/scale";

describe("formatacao em pt-BR", () => {
  it("separa milhares com ponto e decimais com virgula", () => {
    expect(int(27049049)).toBe("27.049.049");
    expect(pct(77.81)).toBe("77,8%");
    expect(pct(0.02, 2)).toBe("0,02%");
  });
  it("abrevia em mil, mi e bi, e por extenso quando pedido", () => {
    expect(compact(950)).toBe("950");
    expect(compact(12300)).toBe("12,3 mil");
    expect(compact(27049049)).toBe("27,0 mi");
    expect(compact(27049049, true)).toBe("27,0 milhões");
    expect(compact(2.5e9, true)).toBe("2,5 bilhões");
    expect(compact(1_000_000)).toBe("1,0 mi");
    expect(compact(9_500_000)).toBe("9,5 mi");
    expect(compact(999_999)).toBe("1.000,0 mil");
  });
  it("mostra bytes em unidades binarias e tempos em minutos", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(5621242597)).toBe("5,2 GiB");
    expect(bytes(1000)).toBe("1.000 B");
    expect(bytes(1024)).toBe("1,0 KiB");
    expect(seconds(0.03)).toBe("0,0 s");
    expect(seconds(85.8)).toBe("1 min 26 s");
  });
  it("traduz o mes de referencia e rotula UF vazia", () => {
    expect(monthLabel("2026-10")).toBe("out/2026");
    expect(monthLabel("lixo")).toBe("lixo");
    expect(ufLabel(null)).toBe("Sem UF informada");
    expect(ufLabel("SP")).toBe("SP");
  });
  it("capitaliza nomes de municipio mantendo preposicoes", () => {
    expect(titleCase("SAO JOSE DOS CAMPOS")).toBe("Sao Jose dos Campos");
    expect(titleCase("RIO DE JANEIRO")).toBe("Rio de Janeiro");
  });
});

describe("escalas", () => {
  it("escolhe passos redondos", () => {
    expect(niceStep(5_100_000, 4)).toBe(2_000_000);
    expect(niceStep(100, 4)).toBe(25);
    expect(niceStep(0, 4)).toBe(1);
  });
  it("cobre o maximo com ticks que comecam em zero", () => {
    const { ticks, top } = niceTicks(5_100_000, 4);
    expect(ticks[0]).toBe(0);
    expect(top).toBeGreaterThanOrEqual(5_100_000);
    expect(ticks.at(-1)).toBe(top);
  });
  it("limita os rotulos do eixo x e mantem o primeiro", () => {
    expect(evenTicks(27, 6)).toEqual([0, 5, 10, 15, 20, 25]);
    expect(evenTicks(0, 5)).toEqual([]);
    expect(evenTicks(3, 10)).toEqual([0, 1, 2]);
  });
});

describe("leitor de markdown", () => {
  it("separa paragrafos, tabelas e codigo", () => {
    const blocks = parseMarkdown(
      ["Texto um", "continua.", "", "| UF | Total |", "|---|---:|", "| SP | 10 |", "| MG | 7 |", "", "```sql", "SELECT 1", "```"].join("\n"),
    );
    expect(blocks).toEqual([
      { type: "paragraph", text: "Texto um continua." },
      { type: "table", head: ["UF", "Total"], rows: [["SP", "10"], ["MG", "7"]] },
      { type: "code", text: "SELECT 1" },
    ]);
  });
  it("completa linhas curtas da tabela", () => {
    const [table] = parseMarkdown("| a | b |\n|---|---|\n| 1 |");
    expect(table).toEqual({ type: "table", head: ["a", "b"], rows: [["1", ""]] });
  });
  it("nunca devolve html: marcas ficam como texto", () => {
    const [block] = parseMarkdown("<img src=x onerror=alert(1)> e <script>x</script>");
    expect(block).toEqual({ type: "paragraph", text: "<img src=x onerror=alert(1)> e <script>x</script>" });
  });
  it("destaca negrito sem interpretar o resto", () => {
    expect(inlineParts("a **b** c")).toEqual([
      { text: "a ", bold: false },
      { text: "b", bold: true },
      { text: " c", bold: false },
    ]);
    expect(inlineParts("sem marcas")).toEqual([{ text: "sem marcas", bold: false }]);
  });
  it("le listas numeradas e com marcadores, separadas do paragrafo", () => {
    const blocks = parseMarkdown(["As 5 UFs:", "", "1. SP: 8", "2. MG: 7", "", "Motivos:", "- lê CSVs;", "- grava **Parquet**."].join("\n"));
    expect(blocks).toEqual([
      { type: "paragraph", text: "As 5 UFs:" },
      { type: "list", ordered: true, start: 1, items: ["SP: 8", "MG: 7"] },
      { type: "paragraph", text: "Motivos:" },
      { type: "list", ordered: false, start: 1, items: ["lê CSVs;", "grava **Parquet**."] },
    ]);
  });
  it("lista colada ao paragrafo, sem linha em branco, tambem vira lista", () => {
    expect(parseMarkdown("Total:\n- a\n- b")).toEqual([
      { type: "paragraph", text: "Total:" },
      { type: "list", ordered: false, start: 1, items: ["a", "b"] },
    ]);
  });
  it("numero no meio da frase nao vira lista", () => {
    expect(parseMarkdown("Em 2026-10 havia 5 UFs.\nSP lidera.")).toEqual([
      { type: "paragraph", text: "Em 2026-10 havia 5 UFs. SP lidera." },
    ]);
  });
  it("fecha um bloco de codigo sem fim", () => {
    expect(parseMarkdown("```\nSELECT 1")).toEqual([{ type: "code", text: "SELECT 1" }]);
  });
});

describe("resposta da API", () => {
  const ok = { status: "answered", message: "Resposta.", sql: "SELECT 1", columns: ["a"], rows: [[1], ["x"], [null]], truncated: false };
  it("entrega a resposta com consulta e linhas", () => {
    const outcome = interpret(200, ok, "pergunta?");
    expect(outcome.kind).toBe("answered");
    if (outcome.kind === "answered") {
      expect(outcome.answer.pergunta).toBe("pergunta?");
      expect(outcome.answer.sql).toBe("SELECT 1");
      expect(outcome.answer.linhas).toEqual([[1], ["x"], [null]]);
    }
  });
  it("trata recusa de escopo e falha como recusa com a mensagem", () => {
    expect(interpret(200, { status: "out_of_scope", message: "Não posso." }, "q")).toEqual({ kind: "refused", message: "Não posso." });
    expect(interpret(200, { status: "failed", message: "Falhou." }, "q").kind).toBe("refused");
  });
  it("mostra a mensagem quando o agente esta desligado, indisponivel ou sem orcamento", () => {
    for (const status of ["disabled", "unavailable", "budget"]) {
      expect(interpret(503, { status, message: "pausado" }, "q")).toEqual({ kind: "paused", message: "pausado" });
    }
  });
  it("trata 429 como limite de uso", () => {
    expect(interpret(429, { status: "minute", message: "Muitas perguntas." }, "q").kind).toBe("limited");
  });
  it("cai em mensagem generica para erros do servidor e corpo ilegivel", () => {
    expect(interpret(502, { status: "llm_error", message: "detalhe interno" }, "q").kind).toBe("error");
    expect(interpret(500, null, "q").kind).toBe("error");
    expect(interpret(200, { status: "answered", message: "" }, "q").kind).toBe("error");
  });
});

describe("askAgent", () => {
  const response = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status }));
  it("envia JSON para /ask e interpreta a resposta", async () => {
    let seen: { url: string; init?: RequestInit } | undefined;
    const fake = ((url: string, init?: RequestInit) => {
      seen = { url, init };
      return response(200, { status: "answered", message: "ok", sql: "SELECT 1", columns: ["a"], rows: [[1]] });
    }) as unknown as typeof fetch;
    const outcome = await askAgent("https://exemplo.test/", "  Quantas UFs?  ", fake);
    expect(outcome.kind).toBe("answered");
    expect(seen?.url).toBe("https://exemplo.test/ask");
    expect(seen?.init?.method).toBe("POST");
    expect(new Headers(seen?.init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(seen?.init?.body))).toEqual({ question: "Quantas UFs?" });
  });
  it("nao chama a rede para pergunta vazia ou grande demais", async () => {
    const never = (() => {
      throw new Error("nao deveria chamar");
    }) as unknown as typeof fetch;
    expect((await askAgent("https://x.test", "   ", never)).kind).toBe("refused");
    expect((await askAgent("https://x.test", "a".repeat(301), never)).kind).toBe("refused");
  });
  it("converte falha de rede em erro com mensagem", async () => {
    const down = (() => Promise.reject(new TypeError("falhou"))) as unknown as typeof fetch;
    expect((await askAgent("https://x.test", "pergunta", down)).kind).toBe("error");
  });
  it("aceita corpo que nao e JSON", async () => {
    const html = (() => Promise.resolve(new Response("<html>", { status: 502 }))) as unknown as typeof fetch;
    expect((await askAgent("https://x.test", "pergunta", html)).kind).toBe("error");
  });
});
