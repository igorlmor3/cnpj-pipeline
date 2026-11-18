import { monthLabel } from "./format";
import type { EvolucaoSerie, SiteData } from "./types";

/** Mes publicado imediatamente antes do mes aberto; null no primeiro. */
export function previousMonth(site: SiteData): string | null {
  const index = site.months.indexOf(site.month);
  return index > 0 ? (site.months[index - 1] ?? null) : null;
}

/** Estoque (foto) de um mes na serie de evolucao, ou null sem a serie ou sem o mes. */
export function stockAt(site: SiteData, month: string | null): EvolucaoSerie | null {
  if (!month || !site.evolution) return null;
  return site.evolution.dados.serie.find((row) => row.mes === month) ?? null;
}

/** Faixa de meses que a pagina cobre, como "jul/2026 a out/2026" (so o mes, se for um). */
export function spanLabel(site: SiteData): string {
  const first = site.months[0];
  const last = site.months[site.months.length - 1];
  if (!first || !last || first === last) return monthLabel(site.month);
  return `${monthLabel(first)} a ${monthLabel(last)}`;
}
