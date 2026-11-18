import { type BarItem, BarList, COLORS } from "../components/charts";
import { ChartCard, DataTable, Poster, Row } from "../components/ui";
import { brl, compact, int, monthLabel, pct, textOrDash } from "../lib/format";
import type { SiteData } from "../lib/types";

export function Profile({ site }: { site: SiteData }) {
  const porte: BarItem[] = site.porte.dados.map((row) => ({
    key: row.porte ?? "sem-porte",
    label: textOrDash(row.porte_descricao),
    value: row.participacao_pct,
    text: pct(row.participacao_pct, 2),
    detail: `${textOrDash(row.porte_descricao)}: ${int(row.empresas_ativas)} empresas`,
  }));
  const natureza: BarItem[] = [
    ...site.natureza.dados.map((row) => ({
      key: row.natureza,
      label: row.natureza_descricao,
      value: row.participacao_pct,
      text: pct(row.participacao_pct, 2),
      detail: `${row.natureza_descricao}: ${int(row.empresas_ativas)} empresas`,
    })),
    {
      key: "outras",
      label: "Demais naturezas",
      value: site.natureza.outras.participacao_pct,
      text: pct(site.natureza.outras.participacao_pct, 2),
      detail: `${int(site.natureza.outras.empresas_ativas)} empresas`,
      color: COLORS.secondary,
    },
  ];
  const sections: BarItem[] = [...site.ativosSecao.dados]
    .sort((a, b) => b.estabelecimentos_ativos - a.estabelecimentos_ativos)
    .slice(0, 12)
    .map((row) => ({
      key: row.secao,
      label: `${row.secao} · ${row.secao_descricao}`,
      value: row.estabelecimentos_ativos,
      text: compact(row.estabelecimentos_ativos),
      detail: `${row.secao_descricao}: ${int(row.estabelecimentos_ativos)} estabelecimentos`,
    }));
  const capital = {
    head: ["Porte", "Natureza jurídica", "Empresas", "Capital mediano"],
    numeric: [2, 3],
    rows: site.capital.dados.map((row) => [row.porte_descricao, row.natureza_descricao, int(row.empresas), brl(row.capital_social_mediano)]),
  };
  const groups = site.natureza.grupos.filter((g) => g.grupo);
  return (
    <Row id="perfil" title="Perfil das empresas" hint={`Porte, natureza jurídica e setor em ${monthLabel(site.month)}`}>
      <ChartCard
        title="Empresas ativas por porte"
        subtitle="Parcela do total"
        size="md"
        chart={<BarList items={porte} label="Empresas ativas por porte" max={100} />}
        footer="A barra vai de 0 a 100%."
      />
      <ChartCard
        title="Natureza jurídica"
        subtitle="As 12 mais comuns e o restante"
        size="md"
        chart={<BarList items={natureza} label="Empresas ativas por natureza jurídica" max={100} />}
        table={{
          head: ["Grupo", "Empresas", "Parcela"],
          numeric: [1, 2],
          rows: groups.map((g) => [g.grupo ?? "", int(g.empresas_ativas), pct(g.participacao_pct, 2)]),
        }}
        footer="A tabela mostra os grupos de natureza jurídica. A barra vai de 0 a 100%."
      />
      <ChartCard
        title="Estabelecimentos ativos por seção"
        subtitle="As 12 maiores seções da CNAE"
        size="md"
        chart={<BarList items={sections} label="Estabelecimentos ativos por seção da CNAE" />}
      />
      <Poster size="lg">
        <div className="poster__head">
          <div>
            <h3 className="poster__title">Capital social mediano</h3>
            <p className="poster__sub">Por porte e natureza, sem organizações políticas</p>
          </div>
        </div>
        <DataTable spec={capital} label="Capital social mediano por porte e natureza" />
        <p className="poster__foot">
          O capital social é declarado e tem valores extremos. Por isso o site usa medianas e não somas.
        </p>
      </Poster>
    </Row>
  );
}
