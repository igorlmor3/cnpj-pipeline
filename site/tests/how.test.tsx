import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { loadSite } from "../src/lib/data";
import { HowItWasMade, buildSteps } from "../src/sections/HowItWasMade";
import { publicFetch } from "./helpers";

afterEach(cleanup);

describe("Como foi feito", () => {
  it("monta oito etapas com os numeros reais do pipeline", async () => {
    const site = await loadSite("/", publicFetch());
    const steps = buildSteps(site);
    expect(steps.map((s) => s.title)).toEqual(["Download", "Bronze", "Silver", "Gold", "Qualidade", "AWS", "Agente", "API e site"]);
    const byTitle = Object.fromEntries(steps.map((s) => [s.title, s]));
    expect(byTitle.Bronze?.metric).toBe("223,6 mi");
    expect(byTitle.Bronze?.caption).toContain("0 rejeitadas");
    expect(byTitle.Silver?.caption).toContain("1 duplicada removida");
    expect(byTitle.Gold?.text[0]).toContain("13 modelos em SQL do DuckDB: 7 dimensões, 1 fato");
    expect(byTitle.Gold?.text[0]).toContain("5 marts agregados");
    expect(byTitle.Qualidade?.metric).toBe("73");
    expect(byTitle.Download?.caption).toBe("de CSV em 10 tabelas");
  });

  it("mostra etapas, decisoes e links do repositorio", async () => {
    const site = await loadSite("/", publicFetch());
    render(<HowItWasMade site={site} />);
    expect(screen.getByRole("heading", { level: 2, name: "Como foi feito" })).toBeInTheDocument();
    expect(screen.getAllByText(/^Etapa \d$/)).toHaveLength(8);
    expect(screen.getByRole("heading", { level: 2, name: "Decisões de engenharia" })).toBeInTheDocument();
    const docs = screen.getByRole("heading", { name: "Leia no repositório" }).closest("article") as HTMLElement;
    const link = within(docs).getByRole("link", { name: "docs/architecture.md" });
    expect(link).toHaveAttribute("href", "https://github.com/igorlmor3/cnpj-pipeline/blob/main/docs/architecture.md");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
