import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BarList, LineChart } from "../src/components/charts";
import { monthTicks } from "../src/sections/Openings";
import { topUfs } from "../src/sections/States";
import type { UfRow } from "../src/lib/types";

afterEach(cleanup);

const labels = ["2024", "2025", "2026"];
const series = [
  { id: "a", name: "Demais", color: "#e50914", values: [100, 200, 150] },
  { id: "b", name: "Org", color: "#3987e5", values: [10, 20, 5] },
];
const fmt = (n: number) => `${n} un`;

describe("LineChart", () => {
  it("expoe o grafico com nome acessivel e legenda para duas series", () => {
    render(<LineChart label="Aberturas" labels={labels} series={series} format={fmt} />);
    expect(screen.getByRole("img", { name: "Aberturas" })).toBeInTheDocument();
    expect(screen.getAllByText("Demais").length).toBeGreaterThan(0);
  });

  it("nao desenha legenda para uma serie so", () => {
    const { container } = render(<LineChart label="x" labels={labels} series={[series[0] as (typeof series)[0]]} format={fmt} />);
    expect(container.querySelector(".legend")).toBeNull();
  });

  it("mostra a dica com os valores ao navegar pelo teclado e esconde com Escape", () => {
    render(<LineChart label="Aberturas" labels={labels} series={series} format={fmt} partialLast />);
    const chart = screen.getByRole("img", { name: "Aberturas" });
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("2024");
    expect(screen.getByRole("status")).toHaveTextContent("100 un");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("2026 (parcial)");
    expect(screen.getByRole("status")).toHaveTextContent("150 un");
    fireEvent.keyDown(chart, { key: "Escape" });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("nao passa do primeiro nem do ultimo ponto", () => {
    render(<LineChart label="x" labels={labels} series={series} format={fmt} />);
    const chart = screen.getByRole("img", { name: "x" });
    for (let i = 0; i < 6; i += 1) fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("2026");
    for (let i = 0; i < 6; i += 1) fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(screen.getByRole("status")).toHaveTextContent("2024");
  });

  it("tracejado so quando o ultimo ponto e parcial", () => {
    const { container, rerender } = render(<LineChart label="x" labels={labels} series={series} format={fmt} partialLast />);
    expect(container.querySelectorAll("path[stroke-dasharray]").length).toBe(2);
    rerender(<LineChart label="x" labels={labels} series={series} format={fmt} />);
    expect(container.querySelectorAll("path[stroke-dasharray]").length).toBe(0);
  });

  it("aceita uma serie vazia sem quebrar", () => {
    render(<LineChart label="vazio" labels={[]} series={[{ id: "a", name: "A", color: "#fff", values: [] }]} format={fmt} />);
    expect(screen.getByRole("img", { name: "vazio" })).toBeInTheDocument();
  });
});

describe("BarList", () => {
  const items = [
    { key: "a", label: "A", value: 50, text: "50" },
    { key: "b", label: "B", value: 25, text: "25" },
    { key: "c", label: "C", value: 0, text: "0" },
  ];
  const widths = (container: HTMLElement) =>
    [...container.querySelectorAll<HTMLElement>(".bars__fill")].map((el) => el.style.width);

  it("escala as barras pelo maior valor", () => {
    const { container } = render(<BarList items={items} label="Lista" />);
    expect(widths(container)).toEqual(["100%", "50%", "0%"]);
  });

  it("respeita um maximo fixo e nunca passa de 100%", () => {
    const { container } = render(<BarList items={items} label="Lista" max={100} />);
    expect(widths(container)).toEqual(["50%", "25%", "0%"]);
    const over = render(<BarList items={[{ key: "x", label: "X", value: 300, text: "300" }]} label="Maior" max={100} />);
    expect(widths(over.container)).toEqual(["100%"]);
  });

  it("nomeia a lista e cada linha para leitores de tela", () => {
    render(<BarList items={items} label="Lista de teste" />);
    expect(screen.getByRole("list", { name: "Lista de teste" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});

describe("auxiliares das secoes", () => {
  it("marca os janeiros de anos pares como rotulos do eixo mensal", () => {
    const labels = ["2015-12", "2016-01", "2016-02", "2017-01", "2018-01"];
    expect(monthTicks(labels)).toEqual([1, 4]);
    expect(monthTicks(labels, 1)).toEqual([1, 3, 4]);
  });

  it("ordena as UFs por empresas ativas e limita o top", () => {
    const row = (uf: string | null, empresas: number) => ({ uf, empresas_ativas: empresas }) as UfRow;
    const top = topUfs([row("A", 5), row("B", 50), row(null, 20), row("C", 1)], 3);
    expect(top.map((r) => r.uf)).toEqual(["B", null, "A"]);
  });
});
