import { type ReactNode, useId, useRef, useState } from "react";

export const Icon = {
  play: (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 4.5v15l13-7.5z" fill="currentColor" />
    </svg>
  ),
  info: (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M12 11v6M12 7.5v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  ),
  left: (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  ),
  right: (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 5l7 7-7 7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  ),
  close: (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  ),
};

/** Fileira horizontal com setas, no estilo de catalogo. */
export function Row({
  id,
  title,
  hint,
  children,
}: {
  id?: string;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const heading = useId();
  const move = (direction: 1 | -1) => {
    const element = scroller.current;
    if (!element || typeof element.scrollBy !== "function") return;
    element.scrollBy({ left: direction * element.clientWidth * 0.85, behavior: "smooth" });
  };
  return (
    <section id={id} className="row" aria-labelledby={heading}>
      <div className="row__head">
        <h2 id={heading}>{title}</h2>
        {hint ? <p>{hint}</p> : null}
      </div>
      <div className="row__viewport">
        <button type="button" className="row__arrow row__arrow--left" aria-label="Rolar para a esquerda" onClick={() => move(-1)}>
          {Icon.left}
        </button>
        <div className="row__scroller" ref={scroller}>
          {children}
        </div>
        <button type="button" className="row__arrow row__arrow--right" aria-label="Rolar para a direita" onClick={() => move(1)}>
          {Icon.right}
        </button>
      </div>
    </section>
  );
}

export type PosterSize = "sm" | "md" | "lg";

export function Poster({ size = "md", children }: { size?: PosterSize; children: ReactNode }) {
  return <article className={`poster poster--${size}`}>{children}</article>;
}

export interface TableSpec {
  head: string[];
  rows: (string | number)[][];
  /** Colunas (por indice) alinhadas a direita. */
  numeric?: number[];
}

export function DataTable({ spec, label }: { spec: TableSpec; label: string }) {
  const numeric = new Set(spec.numeric ?? []);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <table className="table">
        <thead>
          <tr>
            {spec.head.map((cell, i) => (
              <th key={cell} scope="col" className={numeric.has(i) ? "num" : undefined}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((row, r) => (
            <tr key={`${String(row[0])}-${r}`}>
              {row.map((cell, i) => (
                <td key={`${spec.head[i] ?? i}-${r}`} className={numeric.has(i) ? "num" : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Cartao com grafico e, quando ha tabela equivalente, um seletor Grafico/Tabela. */
export function ChartCard({
  title,
  subtitle,
  size = "md",
  chart,
  table,
  footer,
  controls,
}: {
  title: string;
  subtitle?: string;
  size?: PosterSize;
  chart: ReactNode;
  table?: TableSpec;
  footer?: string;
  controls?: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Poster size={size}>
      <div className="poster__head">
        <div style={{ minWidth: 0 }}>
          <h3 className="poster__title">{title}</h3>
          {subtitle ? <p className="poster__sub">{subtitle}</p> : null}
        </div>
        {table ? (
          <div className="seg" role="group" aria-label={`Formato de ${title}`}>
            <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
              Gráfico
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
              Tabela
            </button>
          </div>
        ) : null}
      </div>
      {controls ? <div style={{ marginBottom: "0.6rem" }}>{controls}</div> : null}
      {view === "table" && table ? <DataTable spec={table} label={`Tabela: ${title}`} /> : chart}
      {footer ? <p className="poster__foot">{footer}</p> : null}
    </Poster>
  );
}

export function Kpi({ value, label, note }: { value: string; label: string; note?: string }) {
  return (
    <Poster size="sm">
      <div className="kpi__value">{value}</div>
      <div className="kpi__label">{label}</div>
      {note ? <div className="kpi__note">{note}</div> : null}
    </Poster>
  );
}

export function YearSelect({
  label,
  value,
  years,
  partial,
  onChange,
}: {
  label: string;
  value: number;
  years: number[];
  partial?: number;
  onChange: (year: number) => void;
}) {
  const id = useId();
  return (
    <label htmlFor={id} style={{ fontSize: "0.8rem", color: "var(--color-muted)" }}>
      {label}{" "}
      <select id={id} className="field" value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {years.map((year) => (
          <option key={year} value={year}>
            {year === partial ? `${year} (parcial)` : year}
          </option>
        ))}
      </select>
    </label>
  );
}
