import { type KeyboardEvent, type PointerEvent, useRef, useState } from "react";
import { evenTicks, niceTicks } from "../lib/scale";
import { useElementWidth } from "./hooks";

export const COLORS = {
  primary: "#e50914",
  secondary: "#3987e5",
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#9a9a9a",
  surface: "#181818",
};

export interface BarItem {
  key: string;
  label: string;
  value: number;
  text: string;
  detail?: string;
  color?: string;
}

/** Barras horizontais finas, ancoradas na base, com o valor escrito ao lado (rotulo direto). */
export function BarList({ items, label, max }: { items: BarItem[]; label: string; max?: number }) {
  const top = max ?? Math.max(0, ...items.map((item) => item.value));
  return (
    <div className="bars" role="list" aria-label={label}>
      {items.map((item) => (
        <div className="bars__row" role="listitem" key={item.key} title={item.detail ?? `${item.label}: ${item.text}`}>
          <span className="bars__label">{item.label}</span>
          <span className="bars__track">
            <span
              className="bars__fill"
              style={{
                display: "block",
                width: `${top > 0 ? Math.max(0, Math.min(100, (item.value / top) * 100)) : 0}%`,
                background: item.color ?? COLORS.primary,
              }}
            />
          </span>
          <span className="bars__value">{item.text}</span>
        </div>
      ))}
    </div>
  );
}

export interface LineSeries {
  id: string;
  name: string;
  color: string;
  values: number[];
}

export interface LineChartProps {
  labels: string[];
  series: LineSeries[];
  format: (value: number) => string;
  axisFormat?: (value: number) => string;
  /** Indices que recebem rotulo no eixo x; por padrao, espacados de forma uniforme. */
  tickIndexes?: number[];
  tickLabel?: (label: string) => string;
  /** O ultimo ponto e parcial: o ultimo trecho fica tracejado e o marcador vazado. */
  partialLast?: boolean;
  endLabels?: boolean;
  label: string;
  height?: number;
}

const MARGIN = { top: 14, right: 14, bottom: 26, left: 50 };

export function LineChart({
  labels,
  series,
  format,
  axisFormat,
  tickIndexes,
  tickLabel,
  partialLast = false,
  endLabels = false,
  label,
  height = 250,
}: LineChartProps) {
  const wrapper = useRef<HTMLDivElement>(null);
  const width = useElementWidth(wrapper, 600);
  const [hover, setHover] = useState<number | null>(null);
  const count = labels.length;
  const innerWidth = Math.max(60, width - MARGIN.left - MARGIN.right);
  const innerHeight = height - MARGIN.top - MARGIN.bottom;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const { ticks, top } = niceTicks(max, 4);
  const x = (i: number) => MARGIN.left + (count <= 1 ? innerWidth / 2 : (i * innerWidth) / (count - 1));
  const y = (value: number) => MARGIN.top + innerHeight - (value / top) * innerHeight;
  const xTicks = tickIndexes ?? evenTicks(count, Math.floor(innerWidth / 64));
  const solidEnd = partialLast && count > 1 ? count - 2 : count - 1;

  const path = (values: number[], from: number, to: number) =>
    values
      .slice(from, to + 1)
      .map((value, k) => `${k === 0 ? "M" : "L"}${x(from + k).toFixed(1)} ${y(value).toFixed(1)}`)
      .join(" ");

  const indexAt = (clientX: number, element: Element) => {
    const rect = element.getBoundingClientRect();
    const ratio = (clientX - rect.left - MARGIN.left) / innerWidth;
    return Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1))));
  };
  const onMove = (event: PointerEvent<SVGSVGElement>) => setHover(indexAt(event.clientX, event.currentTarget));
  const onKey = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.key === "ArrowRight") setHover((h) => Math.min(count - 1, (h ?? -1) + 1));
    else if (event.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? count) - 1));
    else if (event.key === "Escape") setHover(null);
    else return;
    event.preventDefault();
  };

  const tipLeft = hover === null ? 0 : x(hover);
  const flip = tipLeft > width * 0.58;
  const isPartial = (i: number) => partialLast && i === count - 1;

  return (
    <div className="chart" ref={wrapper}>
      {series.length > 1 ? (
        <div className="legend" aria-hidden="true">
          {series.map((s) => (
            <span key={s.id}>
              <i className="dot" style={{ background: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      ) : null}
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        onBlur={() => setHover(null)}
        onKeyDown={onKey}
        style={{ touchAction: "pan-y" }}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick)} y2={y(tick)} stroke={tick === 0 ? COLORS.axis : COLORS.grid} strokeWidth={1} />
            <text x={MARGIN.left - 8} y={y(tick)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill={COLORS.muted}>
              {(axisFormat ?? format)(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text key={i} x={x(i)} y={height - 8} textAnchor="middle" fontSize={11} fill={COLORS.muted}>
            {tickLabel ? tickLabel(labels[i] ?? "") : labels[i]}
          </text>
        ))}
        {series.map((s) => (
          <g key={s.id}>
            <path d={path(s.values, 0, solidEnd)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {partialLast && count > 1 ? (
              <path d={path(s.values, count - 2, count - 1)} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray="4 4" />
            ) : null}
            {count > 0 ? (
              <circle
                cx={x(count - 1)}
                cy={y(s.values[count - 1] ?? 0)}
                r={4}
                fill={partialLast ? COLORS.surface : s.color}
                stroke={s.color}
                strokeWidth={2}
              />
            ) : null}
            {endLabels && series.length > 1 && count > 0 ? (
              <text
                x={x(count - 1) - 12}
                y={y(s.values[count - 1] ?? 0) - 16}
                textAnchor="end"
                fontSize={11}
                fill="#c9c9c9"
                stroke={COLORS.surface}
                strokeWidth={3}
                paintOrder="stroke"
              >
                {s.name}
              </text>
            ) : null}
          </g>
        ))}
        {hover !== null ? (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={MARGIN.top} y2={MARGIN.top + innerHeight} stroke="#ffffff" strokeOpacity={0.35} strokeWidth={1} />
            {series.map((s) => (
              <circle key={s.id} cx={x(hover)} cy={y(s.values[hover] ?? 0)} r={5} fill={s.color} stroke={COLORS.surface} strokeWidth={2} />
            ))}
          </g>
        ) : null}
      </svg>
      {hover !== null ? (
        <div
          className="chart__tip"
          role="status"
          style={flip ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }}
        >
          <b>
            {labels[hover]}
            {isPartial(hover) ? " (parcial)" : ""}
          </b>
          {series.map((s) => (
            <div key={s.id}>
              <span>
                <i className="dot" style={{ background: s.color }} />
                {s.name}
              </span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{format(s.values[hover] ?? 0)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
