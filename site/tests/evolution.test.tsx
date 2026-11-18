import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { collectCaveats, loadSite } from "../src/lib/data";
import { signed, signedCompact } from "../src/lib/format";
import type { Evolucao } from "../src/lib/types";
import { evolutionFacts } from "../src/sections/Evolution";
import { publicFetch, readPublic } from "./helpers";

const evolucao = () => readPublic<Evolucao>("data/evolucao.json");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("carregamento de meses", () => {
  it("lista os meses publicados e abre o mais recente por padrao", async () => {
    const site = await loadSite("/cnpj-pipeline/", publicFetch());
    expect(site.months).toEqual(["2026-07", "2026-08", "2026-09", "2026-10"]);
    expect(site.month).toBe("2026-10");
    expect(site.evolution?.meses).toEqual(site.months);
  });

  it("abre o mes pedido e valida o mes de cada arquivo contra ele", async () => {
    const site = await loadSite("/cnpj-pipeline/", publicFetch(), "2026-08");
    expect(site.month).toBe("2026-08");
    expect(site.overview.mes_referencia).toBe("2026-08");
  });

  it("ignora um mes que nao esta publicado", async () => {
    expect((await loadSite("/", publicFetch(), "2019-01")).month).toBe("2026-10");
    expect((await loadSite("/", publicFetch(), "../../etc")).month).toBe("2026-10");
  });

  it("segue sem a serie quando o arquivo falta ou tem outro esquema", async () => {
    expect((await loadSite("/", publicFetch({ "data/evolucao.json": null }))).evolution).toBeNull();
    expect((await loadSite("/", publicFetch({ "data/evolucao.json": { ...evolucao(), esquema: 2 } }))).evolution).toBeNull();
    const broken = { ...evolucao(), dados: { ...evolucao().dados, serie: "nao e lista" } };
    expect((await loadSite("/", publicFetch({ "data/evolucao.json": broken }))).evolution).toBeNull();
  });

  it("resume os totais de cada mes e ignora um pipeline.json de outro mes", async () => {
    const site = await loadSite("/", publicFetch());
    expect(site.runs.map((r) => r.month)).toEqual(site.months);
    expect(site.runs.every((r) => r.linhas > 0 && r.linhas_rejeitadas === 0 && r.linhas_fato > 0)).toBe(true);
    const wrong = { ...readPublic<object>("data/2026-08/pipeline.json"), mes_referencia: "2026-07" };
    const partial = await loadSite("/", publicFetch({ "data/2026-08/pipeline.json": wrong }));
    expect(partial.runs.map((r) => r.month)).toEqual(["2026-07", "2026-09", "2026-10"]);
    const missing = await loadSite("/", publicFetch({ "data/2026-07/pipeline.json": null }));
    expect(missing.runs.map((r) => r.month)).toEqual(["2026-08", "2026-09", "2026-10"]);
  });

  it("junta as ressalvas da serie", async () => {
    const codes = collectCaveats(await loadSite("/", publicFetch())).map((c) => c.code);
    expect(codes).toContain("fotos_mensais");
    expect(new Set(codes).size).toBe(codes.length);
  });
});

describe("serie publicada", () => {
  it("nao carrega campos de identificacao", () => {
    expect(JSON.stringify(evolucao())).not.toMatch(/cnpj_basico|cnpj_ordem|razao_social|nome_fantasia|logradouro|"email"|telefone/i);
  });

  it("a variacao de cada mes fecha com a diferenca dos estoques", () => {
    const { serie, movimentos } = evolucao().dados;
    for (const move of movimentos) {
      const now = serie.find((row) => row.mes === move.mes);
      const before = serie.find((row) => row.mes === move.mes_anterior);
      expect(now && before).toBeTruthy();
      expect((now?.estabelecimentos_ativos ?? 0) - (before?.estabelecimentos_ativos ?? 0)).toBe(move.saldo_ativos);
      expect(move.novos_ativos + move.passaram_a_ativos - move.deixaram_de_ser_ativos - move.removidos_ativos).toBe(move.saldo_ativos);
    }
  });

  it("o saldo por UF soma a variacao do mes", () => {
    const { movimentos, saldo_uf } = evolucao().dados;
    for (const move of movimentos) {
      const total = saldo_uf.filter((row) => row.mes === move.mes).reduce((sum, row) => sum + row.saldo, 0);
      expect(total).toBe(move.saldo_ativos);
    }
  });
});

describe("evolutionFacts", () => {
  it("devolve o movimento, as maiores transicoes e os extremos por UF", () => {
    const facts = evolutionFacts(evolucao(), "2026-10");
    expect(facts.movement?.mes_anterior).toBe("2026-09");
    expect(facts.stock?.estabelecimentos_ativos).toBeGreaterThan(28_000_000);
    expect(facts.transitions.length).toBeLessThanOrEqual(8);
    const values = facts.transitions.map((row) => row.value);
    expect(values).toEqual([...values].sort((a, b) => b - a));
    expect(facts.ufGain.every((row) => row.value > 0)).toBe(true);
    expect(facts.ufLoss.every((row) => row.value < 0)).toBe(true);
    expect(facts.ufGain[0]?.value).toBeGreaterThanOrEqual(facts.ufGain[1]?.value ?? 0);
    expect(facts.lag.reduce((sum, row) => sum + row.share, 0)).toBeCloseTo(100, 5);
  });

  it("o primeiro mes da serie nao tem movimento", () => {
    const facts = evolutionFacts(evolucao(), "2026-07");
    expect(facts.movement).toBeNull();
    expect(facts.stock).not.toBeNull();
    expect(facts.transitions).toEqual([]);
    expect(facts.ufGain).toEqual([]);
  });
});

describe("formatacao com sinal", () => {
  it("escreve o sinal explicitamente", () => {
    expect(signed(348635)).toBe("+348.635");
    expect(signed(-5)).toBe("-5");
    expect(signed(0)).toBe("0");
    expect(signedCompact(348635)).toBe("+348,6 mil");
    expect(signedCompact(-24282)).toBe("-24,3 mil");
  });
});

function install(overrides: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", vi.fn(publicFetch(overrides)));
}

describe("pagina de evolucao", () => {
  it("mostra a secao com os movimentos do mes aberto", async () => {
    install();
    render(<App />);
    const heading = await screen.findByRole("heading", { level: 2, name: "Evolução entre meses" });
    const section = heading.closest("section") as HTMLElement;
    expect(within(section).getByText(/Variação líquida de ativos desde set\/2026/)).toBeInTheDocument();
    expect(within(section).getByRole("heading", { name: "Mudanças de situação cadastral" })).toBeInTheDocument();
    expect(within(section).getByText("As 6 transições mais comuns em out/2026")).toBeInTheDocument();
    expect(within(section).getAllByText("+117,5 mil").length).toBeGreaterThanOrEqual(2); // indicador e barra do mes
  });

  it("mostra os totais de cada mes processado na secao do pipeline", async () => {
    install();
    render(<App />);
    const card = (await screen.findByRole("heading", { name: "Mês a mês" })).closest("article") as HTMLElement;
    const rows = within(card).getAllByRole("row");
    expect(rows).toHaveLength(5);
    expect(within(rows[1] as HTMLElement).getByText("jul/2026")).toBeInTheDocument();
    expect(within(rows[4] as HTMLElement).getByText("out/2026")).toBeInTheDocument();
    expect(within(rows[4] as HTMLElement).getByText("73 (51 bloqueantes)")).toBeInTheDocument();
  });

  it("troca de mes pelo seletor do topo, recarrega os dados e guarda o mes no endereco", async () => {
    install();
    render(<App />);
    const select = (await screen.findByLabelText("Mês")) as HTMLSelectElement;
    expect(select.value).toBe("2026-10");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["2026-07", "2026-08", "2026-09", "2026-10"]);
    fireEvent.change(select, { target: { value: "2026-08" } });
    await waitFor(() => expect((screen.getByLabelText("Mês") as HTMLSelectElement).value).toBe("2026-08"));
    expect(await screen.findByText(/Variação líquida de ativos desde jul\/2026/)).toBeInTheDocument();
    expect(window.location.search).toBe("?mes=2026-08");
    fireEvent.change(screen.getByLabelText("Mês"), { target: { value: "2026-10" } });
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("abre o mes pedido no endereco", async () => {
    window.history.replaceState(null, "", "/?mes=2026-09");
    install();
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect((screen.getByLabelText("Mês") as HTMLSelectElement).value).toBe("2026-09");
  });

  it("explica que o primeiro mes nao tem foto anterior", async () => {
    window.history.replaceState(null, "", "/?mes=2026-07");
    install();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Primeiro mês da série" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Mudanças de situação cadastral" })).toBeNull();
  });

  it("avisa e mantem o mes atual quando o outro mes nao carrega", async () => {
    install({ "data/2026-09/uf.json": null });
    render(<App />);
    const select = (await screen.findByLabelText("Mês")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "2026-09" } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/Não foi possível carregar set\/2026/);
    expect((screen.getByLabelText("Mês") as HTMLSelectElement).value).toBe("2026-10");
  });

  it("sem a serie, a secao e o link de navegacao somem", async () => {
    install({ "data/evolucao.json": null });
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("heading", { level: 2, name: "Evolução entre meses" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Evolução" })).toBeNull();
  });
});
