import { type BarItem, BarList } from "../components/charts";
import { ChartCard, Row } from "../components/ui";
import { compact, int, monthLabel, textOrDash, titleCase } from "../lib/format";
import type { SiteData } from "../lib/types";

export function Cities({ site }: { site: SiteData }) {
  const rows = site.municipios.dados;
  const name = (uf: string | null, city: string | null) => `${titleCase(textOrDash(city))}${uf ? ` · ${uf}` : ""}`;
  const bars: BarItem[] = rows.slice(0, 12).map((row) => ({
    key: `${row.uf ?? "-"}-${row.municipio_nome ?? "-"}`,
    label: name(row.uf, row.municipio_nome),
    value: row.estabelecimentos_ativos,
    text: compact(row.estabelecimentos_ativos),
    detail: `${name(row.uf, row.municipio_nome)}: ${int(row.estabelecimentos_ativos)} estabelecimentos`,
  }));
  return (
    <Row id="municipios" title="Municípios" hint={`Os 50 com mais estabelecimentos ativos em ${monthLabel(site.month)}`}>
      <ChartCard
        title="Os 12 maiores"
        subtitle="Estabelecimentos ativos"
        size="md"
        chart={<BarList items={bars} label="Municípios com mais estabelecimentos ativos" />}
        footer="Município nunca é cruzado com atividade econômica."
      />
      <ChartCard
        title="Top 50"
        subtitle="Estabelecimentos e matrizes ativas"
        size="lg"
        chart={
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Os 50 municípios com mais estabelecimentos ativos">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Município</th>
                  <th scope="col" className="num">Estabelecimentos</th>
                  <th scope="col" className="num">Matrizes</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.posicao}>
                    <td>{row.posicao}</td>
                    <td>{name(row.uf, row.municipio_nome)}</td>
                    <td className="num">{int(row.estabelecimentos_ativos)}</td>
                    <td className="num">{int(row.matrizes_ativas)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        }
      />
    </Row>
  );
}
