import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { loadSite } from "../src/lib/data";
import { signedPp } from "../src/lib/format";
import { previousMonth, spanLabel, stockAt } from "../src/lib/series";
import { Agent } from "../src/sections/Agent";
import { Hero } from "../src/sections/Hero";
import { Numbers } from "../src/sections/Numbers";
import { States } from "../src/sections/States";
import { publicFetch } from "./helpers";

afterEach(cleanup);

describe("paginas conectadas entre os meses", () => {
  it("acha o mes anterior e o estoque dele", async () => {
    const site = await loadSite("/", publicFetch(), "2026-09");
    expect(previousMonth(site)).toBe("2026-08");
    expect(stockAt(site, "2026-08")?.mes).toBe("2026-08");
    expect(stockAt(site, null)).toBeNull();
    const first = await loadSite("/", publicFetch(), "2026-07");
    expect(previousMonth(first)).toBeNull();
  });

  it("descreve a faixa de meses", async () => {
    const site = await loadSite("/", publicFetch());
    expect(spanLabel(site)).toBe("jul/2026 a out/2026");
  });

  it("formata diferenca em pontos percentuais", () => {
    expect(signedPp(0.04)).toBe("+0,04 p.p.");
    expect(signedPp(-1.5)).toBe("-1,50 p.p.");
    expect(signedPp(0)).toBe("0,00 p.p.");
  });

  it("mostra no topo o mes exibido e a faixa", async () => {
    const site = await loadSite("/", publicFetch(), "2026-08");
    render(<Hero site={site} onMonth={() => undefined} />);
    expect(screen.getByText("mês exibido")).toBeTruthy();
    expect(screen.getByText(/jul\/2026 a out\/2026/)).toBeTruthy();
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
  });

  it("mostra a variacao desde o mes anterior nos numeros", async () => {
    const site = await loadSite("/", publicFetch(), "2026-10");
    render(<Numbers site={site} />);
    expect(screen.getAllByText(/desde set\/2026/).length).toBeGreaterThanOrEqual(2);
  });

  it("nao mostra variacao no primeiro mes", async () => {
    const site = await loadSite("/", publicFetch(), "2026-07");
    render(<Numbers site={site} />);
    expect(screen.queryByText(/desde/)).toBeNull();
  });

  it("mostra a variacao por UF no top 10", async () => {
    const site = await loadSite("/", publicFetch(), "2026-10");
    render(<States site={site} />);
    expect(screen.getAllByText(/empresas$/).length).toBeGreaterThanOrEqual(10);
  });

  it("avisa que o agente consulta o mes mais recente", async () => {
    const site = await loadSite("/", publicFetch(), "2026-08");
    expect(site.agentMonth).toBe("2026-10");
    expect(site.examples).not.toBeNull();
    render(<Agent site={site} />);
    expect(screen.getByText(/consulta sempre o mês mais recente/)).toBeTruthy();
  });
});
