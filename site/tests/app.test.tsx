import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { publicFetch } from "./helpers";

const EXAMPLES = {
  esquema: 1,
  mes_referencia: "2026-10",
  modelo: "modelo-x",
  exemplos: [
    {
      id: "ranking",
      pergunta: "Quais são as 5 UFs com mais empresas ativas?",
      status: "answered",
      resposta: "São Paulo lidera.\n\n| UF | Empresas |\n|---|---|\n| SP | 8 |",
      sql: "SELECT uf FROM mart LIMIT 5",
      colunas: ["uf", "empresas"],
      linhas: [["SP", 8]],
      truncado: false,
      trajeto: {
        consultas: [
          { sql: "SELECT uf FROM tabela_nao_permitida", ok: false, erro: "tabela nao permitida", linhas: null, cortado: null, bytes_lidos: null },
          { sql: "SELECT uf FROM mart LIMIT 5", ok: true, erro: null, linhas: 5, cortado: false, bytes_lidos: 2048 },
        ],
        buscas_docs: [{ consulta: "por que duckdb", ok: true, fontes: ["docs/architecture.md, secao Decisoes"] }],
        chamadas_modelo: 3,
        tokens_entrada: 12000,
        tokens_saida: 500,
        custo_usd: 0.00152,
        fidelidade: { numeros_verificados: 8, fiel: true, sem_apoio: [] },
      },
    },
    { id: "fora", pergunta: "Qual o endereço do CNPJ 00.000.000/0001-91?", status: "out_of_scope", resposta: "Não informo dados de uma empresa específica." },
  ],
};

function install(overrides: Record<string, unknown> = {}, ask?: (body: unknown) => Response) {
  const base = publicFetch(overrides);
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/ask")) {
        return Promise.resolve(ask ? ask(JSON.parse(String(init?.body))) : new Response("{}", { status: 500 }));
      }
      return base(input, init);
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("pagina", () => {
  it("mostra o carregamento e depois as secoes com numeros reais", async () => {
    install();
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent(/Carregando/);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(/cadastro de empresas/);
    for (const name of ["Em números", "Top 10 estados em empresas ativas", "Aberturas de estabelecimentos", "Perfil das empresas", "Municípios", "Sócios", "O pipeline em números", "Antes de interpretar"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.getAllByText("27,0 mi").length).toBeGreaterThan(0);
    expect(screen.getByText("São Paulo", { selector: ".top10__name" })).toBeInTheDocument();
  });

  it("explica o problema quando os dados nao carregam", async () => {
    install({ "data/months.json": null });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Não foi possível carregar/);
  });

  it("explica o que sao Simples e MEI em uma secao propria", async () => {
    install();
    render(<App />);
    const section = (await screen.findByRole("heading", { level: 2, name: "O que são Simples e MEI" })).closest("section") as HTMLElement;
    expect(within(section).getByRole("heading", { level: 3, name: "Simples Nacional" })).toBeInTheDocument();
    expect(within(section).getByRole("heading", { level: 3, name: /^MEI, Microempreendedor Individual/ })).toBeInTheDocument();
    expect(within(section).getByText(/única guia mensal/)).toBeInTheDocument();
    expect(within(section).getByText(/Todo MEI é optante pelo Simples/)).toBeInTheDocument();
    expect(screen.getByText(/categoria dentro do Simples Nacional/)).toBeInTheDocument();
  });

  it("troca o grafico anual pela tabela equivalente", async () => {
    install();
    render(<App />);
    const card = (await screen.findByRole("heading", { name: "Aberturas por ano" })).closest("article") as HTMLElement;
    expect(within(card).getByRole("img")).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Tabela" }));
    const table = within(card).getByRole("table");
    expect(within(table).getByText("2026 (parcial)")).toBeInTheDocument();
    expect(within(card).queryByRole("img")).toBeNull();
  });

  it("muda os dados ao escolher outro ano", async () => {
    install();
    render(<App />);
    const card = (await screen.findByRole("heading", { name: "Aberturas por UF" })).closest("article") as HTMLElement;
    const select = within(card).getByLabelText("Ano") as HTMLSelectElement;
    expect(select.value).toBe("2025");
    const before = within(card).getAllByRole("listitem").map((n) => n.textContent);
    fireEvent.change(select, { target: { value: "2018" } });
    const after = within(card).getAllByRole("listitem").map((n) => n.textContent);
    expect(after).not.toEqual(before);
    expect(within(card).getByRole("list")).toHaveAccessibleName("Aberturas por UF em 2018");
  });
});

describe("agente", () => {
  it("avisa quando o resultado da consulta foi limitado", async () => {
    const limited = { ...EXAMPLES, exemplos: [{ ...EXAMPLES.exemplos[0], truncado: true }] };
    install({ "data/2026-10/agent_examples.json": limited });
    render(<App />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Quais são as 5 UFs/ }))[0] as HTMLElement);
    expect(within(screen.getByRole("dialog")).getByText(/limitado e pode ter mais linhas/)).toBeInTheDocument();
  });

  it("nao avisa de limite quando o resultado esta completo", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Quais são as 5 UFs/ }))[0] as HTMLElement);
    expect(within(screen.getByRole("dialog")).queryByText(/limitado e pode ter mais linhas/)).toBeNull();
  });

  it("abre e fecha a resposta gravada, mostrando SQL e tabela como texto", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Quais são as 5 UFs/ }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("São Paulo lidera.")).toBeInTheDocument();
    expect(within(dialog).getAllByText("SELECT uf FROM mart LIMIT 5")).toHaveLength(2);
    expect(within(dialog).getAllByRole("table").length).toBeGreaterThan(0);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("mostra como a resposta gravada chegou la, a partir do trajeto", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Quais são as 5 UFs/ }))[0] as HTMLElement);
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("Como chegou lá")).toBeInTheDocument();
    expect(dialog.getByText("Consulta SQL 1: não executada")).toBeInTheDocument();
    expect(dialog.getByText(/tabela nao permitida/)).toBeInTheDocument();
    expect(dialog.getByText("Consulta SQL 2: executada")).toBeInTheDocument();
    expect(dialog.getByText(/5 linhas, resultado completo, 2 KiB lidos no Athena/)).toBeInTheDocument();
    expect(dialog.getByText(/Fontes: docs\/architecture.md, secao Decisoes/)).toBeInTheDocument();
    expect(dialog.getByText(/3 chamadas, 12\.000 tokens de entrada e 500 de saída, custo de US\$ 0,0015/)).toBeInTheDocument();
    expect(dialog.getByText(/8 números do texto foram conferidos e todos aparecem/)).toBeInTheDocument();
  });

  it("sem trajeto no exemplo, nao mostra a secao", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /endereço do CNPJ/ }));
    expect(within(screen.getByRole("dialog")).queryByText("Como chegou lá")).toBeNull();
  });

  it("mostra o trajeto devolvido pela API na resposta ao vivo", async () => {
    install({}, () =>
      new Response(
        JSON.stringify({
          status: "answered",
          message: "Existem 27 UFs.",
          sql: "SELECT 1",
          columns: ["n"],
          rows: [[27]],
          trace: {
            queries: [{ sql: "SELECT 1", ok: true, error: null, rows: 1, cut: false, bytes_scanned: 100 }],
            doc_searches: [],
            llm_calls: 2,
            input_tokens: 100,
            output_tokens: 20,
            cost_usd: 0.0002,
            fidelity: { numbers_checked: 1, faithful: true, unsupported: [] },
          },
        }),
        { status: 200 },
      ),
    );
    render(<App />);
    const box = await screen.findByLabelText("Pergunta sobre o cadastro de CNPJ");
    const user = userEvent.setup();
    await user.type(box, "Quantas UFs existem?");
    await user.click(screen.getByRole("button", { name: /Perguntar$/ }));
    expect(await screen.findByText("Consulta SQL 1: executada")).toBeInTheDocument();
    expect(screen.getByText(/1 número do texto foi conferido e todos aparecem/)).toBeInTheDocument();
  });

  it("explica que as respostas do agente excluem as organizacoes politicas", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    expect(await screen.findByText(/excluem as organizações políticas/)).toBeInTheDocument();
  });

  it("nao mostra essa explicacao sem respostas gravadas", async () => {
    install({ "data/2026-10/agent_examples.json": null });
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/excluem as organizações políticas/)).toBeNull();
  });

  it("mostra a recusa gravada para pergunta fora do escopo", async () => {
    install({ "data/2026-10/agent_examples.json": EXAMPLES });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /endereço do CNPJ/ }));
    expect(within(screen.getByRole("dialog")).getByText(/Não informo dados/)).toBeInTheDocument();
  });

  it("envia a pergunta e mostra a resposta ao vivo", async () => {
    install({}, (body) => {
      expect(body).toEqual({ question: "Quantas UFs existem?" });
      return new Response(JSON.stringify({ status: "answered", message: "Existem 27 UFs.", sql: "SELECT 1", columns: ["n"], rows: [[27]], truncated: false }), { status: 200 });
    });
    render(<App />);
    const box = await screen.findByLabelText("Pergunta sobre o cadastro de CNPJ");
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: /Perguntar$/ });
    expect(button).toBeDisabled();
    await user.type(box, "Quantas UFs existem?");
    await user.click(button);
    expect(await screen.findByText("Existem 27 UFs.")).toBeInTheDocument();
  });

  it.each([
    [503, { status: "disabled", message: "O agente está desligado no momento." }, /desligado/],
    [429, { status: "minute", message: "Muitas perguntas em pouco tempo." }, /Muitas perguntas/],
    [500, { status: "internal_error", message: "segredo interno" }, /Não foi possível obter uma resposta/],
  ])("trata a resposta %i sem quebrar a pagina", async (code, payload, expected) => {
    install({}, () => new Response(JSON.stringify(payload), { status: code }));
    render(<App />);
    const box = await screen.findByLabelText("Pergunta sobre o cadastro de CNPJ");
    const user = userEvent.setup();
    await user.type(box, "pergunta qualquer");
    await user.click(screen.getByRole("button", { name: /Perguntar$/ }));
    await waitFor(() => expect(screen.getByText(expected)).toBeInTheDocument());
    expect(screen.queryByText("segredo interno")).toBeNull();
  });

  it("sem endereco da API avisa que o agente ao vivo nao esta conectado", async () => {
    install({ "config.json": { apiUrl: "", repoUrl: "https://github.com/igorlmor3/cnpj-pipeline" } });
    render(<App />);
    expect(await screen.findByText(/agente ao vivo não está conectado/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Pergunta sobre o cadastro de CNPJ")).toBeNull();
  });

  it("nao interpreta html vindo da resposta", async () => {
    install({}, () => new Response(JSON.stringify({ status: "answered", message: "<img src=x onerror=alert(1)> **ok**", sql: "SELECT 1", columns: ["a"], rows: [[1]] }), { status: 200 }));
    const { container } = render(<App />);
    const box = await screen.findByLabelText("Pergunta sobre o cadastro de CNPJ");
    const user = userEvent.setup();
    await user.type(box, "pergunta");
    await user.click(screen.getByRole("button", { name: /Perguntar$/ }));
    await screen.findByText(/onerror=alert/);
    expect(container.querySelector("img")).toBeNull();
  });
});
