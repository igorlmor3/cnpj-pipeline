import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { loadSite } from "../src/lib/data";
import { HowItWasMade, buildSteps } from "../src/sections/HowItWasMade";
import { publicFetch } from "./helpers";

afterEach(cleanup);

describe("Como foi feito", () => {
  it("monta as etapas com os numeros reais do pipeline", async () => {
    const site = await loadSite("/", publicFetch());
    const steps = buildSteps(site);
    expect(steps.map((s) => s.title)).toEqual(["Download", "Bronze", "Silver", "Gold", "Qualidade", "Orquestração", "Evolução", "AWS", "Agente", "API e site"]);
    const byTitle = Object.fromEntries(steps.map((s) => [s.title, s]));
    expect(byTitle.Bronze?.metric).toBe("885,0 mi");
    expect(byTitle.Bronze?.caption).toContain("0 rejeitadas");
    expect(byTitle.Bronze?.caption).toBe("linhas lidas em 4 meses, de jul/2026 a out/2026, 0 rejeitadas. 223,6 mi em out/2026");
    expect(byTitle.Silver?.caption).toContain("1 duplicada removida");
    expect(byTitle.Silver?.metric).toBe("885,0 mi");
    expect(byTitle.Gold?.text[0]).toContain("13 modelos por mês em SQL do DuckDB: 7 dimensões, 1 fato");
    expect(byTitle.Gold?.text.join(" ")).toContain("mart_movimentos");
    expect(byTitle.Gold?.text[0]).toContain("5 marts agregados");
    expect(byTitle.Qualidade?.metric).toBe("73");
    expect(byTitle.Download?.metric).toBe("106,4 GiB");
    expect(byTitle.Download?.caption).toBe("de CSV em 4 meses, de jul/2026 a out/2026. 26,9 GiB em out/2026, em 10 tabelas");
    expect(byTitle.Qualidade?.caption).toContain("Todas passaram nos 4 meses");
    expect(byTitle.Download?.text[0]).toContain("processou 4 meses, de jul/2026 a out/2026");
    expect(byTitle.Orquestração?.metric).toBe("Airflow");
    expect(byTitle.Evolução?.metric).toBe("1,5 mi");
    expect(byTitle.Evolução?.caption).toBe("CNPJs novos em 3 comparações entre meses");
    expect(byTitle.Agente?.metric).toBe("24 de 25");
  });

  it("sem a serie de evolucao, a etapa de evolucao some", async () => {
    const site = await loadSite("/", publicFetch({ "data/evolucao.json": null }));
    expect(buildSteps(site).map((s) => s.title)).not.toContain("Evolução");
  });

  it("mostra etapas, decisoes e links do repositorio", async () => {
    const site = await loadSite("/", publicFetch());
    render(<HowItWasMade site={site} />);
    expect(screen.getByRole("heading", { level: 2, name: "Como foi feito" })).toBeInTheDocument();
    expect(screen.getAllByText(/^Etapa \d+$/)).toHaveLength(10);
    expect(screen.getByRole("heading", { level: 2, name: "Decisões de engenharia" })).toBeInTheDocument();
    const docs = screen.getByRole("heading", { name: "Leia no repositório" }).closest("article") as HTMLElement;
    const link = within(docs).getByRole("link", { name: "docs/architecture.md" });
    expect(link).toHaveAttribute("href", "https://github.com/igorlmor3/cnpj-pipeline/blob/main/docs/architecture.md");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
