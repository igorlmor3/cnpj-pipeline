import { type BarItem, BarList, COLORS } from "../components/charts";
import { ChartCard, Row } from "../components/ui";
import { compact, int, pct } from "../lib/format";
import type { SiteData } from "../lib/types";

export function Partners({ site }: { site: SiteData }) {
  const total = site.socios.tipos.reduce((sum, tipo) => sum + tipo.socios, 0);
  const tipos: BarItem[] = site.socios.tipos.map((tipo) => ({
    key: tipo.codigo,
    label: tipo.descricao,
    value: tipo.socios,
    text: total > 0 ? pct((tipo.socios / total) * 100, 2) : "-",
    detail: `${tipo.descricao}: ${int(tipo.socios)} vínculos`,
    color: COLORS.secondary,
  }));
  const byQualification = new Map<string, { label: string; socios: number }>();
  for (const row of site.socios.dados) {
    const current = byQualification.get(row.qualificacao);
    byQualification.set(row.qualificacao, {
      label: row.descricao,
      socios: (current?.socios ?? 0) + row.socios,
    });
  }
  const qualifications: BarItem[] = [...byQualification]
    .sort((a, b) => b[1].socios - a[1].socios)
    .slice(0, 10)
    .map(([code, item]) => ({
      key: code,
      label: item.label,
      value: item.socios,
      text: compact(item.socios),
      detail: `${item.label}: ${int(item.socios)} vínculos`,
    }));
  return (
    <Row id="socios" title="Sócios" hint="Apenas contagens agregadas: nenhum nome é publicado">
      <ChartCard
        title="Tipo de sócio"
        subtitle="Parcela dos vínculos"
        size="md"
        chart={<BarList items={tipos} label="Vínculos de sócio por tipo" max={total} />}
        footer={`Soma de ${int(total)} vínculos nos recortes publicados. A barra vai de 0 a 100%.`}
      />
      <ChartCard
        title="Qualificações mais comuns"
        subtitle="Vínculos, somando os tipos de sócio"
        size="md"
        chart={<BarList items={qualifications} label="Qualificações de sócio mais comuns" />}
        footer={
          site.socios.celulas_suprimidas
            ? `${site.socios.celulas_suprimidas} recortes com menos de 5 unidades não são publicados.`
            : undefined
        }
      />
    </Row>
  );
}
