import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { loadSite } from "../src/lib/data";
import { SimplesMei, simplesFacts } from "../src/sections/SimplesMei";
import { publicFetch } from "./helpers";

afterEach(cleanup);

describe("Simples e MEI", () => {
  it("deriva os percentuais a partir da visao geral", async () => {
    const site = await loadSite("/", publicFetch());
    const t = site.overview.dados.todas;
    const f = simplesFacts(site);
    expect(f.optantesSimples).toBe(t.optantes_simples);
    expect(f.optantesMei).toBe(t.optantes_mei);
    expect(f.pctForaDoSimples).toBeCloseTo(100 - t.pct_simples, 10);
    expect(f.pctMeiDentroDoSimples).toBeCloseTo((t.optantes_mei / t.optantes_simples) * 100, 10);
    expect(f.pctMeiDentroDoSimples).toBeGreaterThan(0);
    expect(f.pctMeiDentroDoSimples).toBeLessThan(100);
  });

  it("nao divide por zero sem optantes", async () => {
    const site = await loadSite("/", publicFetch());
    site.overview.dados.todas.optantes_simples = 0;
    site.overview.dados.todas.pct_simples = 100.5;
    const f = simplesFacts(site);
    expect(f.pctMeiDentroDoSimples).toBe(0);
    expect(f.pctForaDoSimples).toBe(0);
  });

  it("mostra os tres cartoes com os numeros", async () => {
    const site = await loadSite("/", publicFetch());
    render(<SimplesMei site={site} />);
    const f = simplesFacts(site);
    expect(screen.getAllByRole("article")).toHaveLength(3);
    expect(screen.getByText(/dos optantes pelo Simples são MEI/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`${f.optantesMei.toLocaleString("pt-BR")} empresas`))).toBeInTheDocument();
  });
});
