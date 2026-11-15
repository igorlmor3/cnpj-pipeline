import { useMemo, useState } from "react";
import { type BarItem, BarList, COLORS, LineChart } from "../components/charts";
import { ChartCard, Row, YearSelect } from "../components/ui";
import { compact, int, ufLabel } from "../lib/format";
import type { SiteData } from "../lib/types";

export function monthTicks(labels: string[], everyYears = 2): number[] {
  const out: number[] = [];
  labels.forEach((label, i) => {
    const match = /^(\d{4})-01$/.exec(label);
    if (match && Number(match[1]) % everyYears === 0) out.push(i);
  });
  return out;
}

export function Openings({ site }: { site: SiteData }) {
  const years = site.aberturasAno.dados;
  const months = site.aberturasMes.dados;
  const partialYear = site.aberturasAno.dados.find((row) => row.parcial)?.ano;
  const sectionYears = useMemo(
    () => [...new Set(site.aberturasSecaoAno.dados.map((row) => row.ano))].sort((a, b) => b - a),
    [site.aberturasSecaoAno.dados],
  );
  const ufYears = useMemo(
    () => [...new Set(site.aberturasUfAno.dados.map((row) => row.ano))].sort((a, b) => b - a),
    [site.aberturasUfAno.dados],
  );
  const lastFull = (list: number[]) => list.find((year) => year !== partialYear) ?? list[0] ?? 0;
  const [sectionYear, setSectionYear] = useState(() => lastFull(sectionYears));
  const [ufYear, setUfYear] = useState(() => lastFull(ufYears));

  const sectionName = useMemo(
    () => new Map(site.aberturasSecaoAno.secoes.map((s) => [s.codigo, s.descricao])),
    [site.aberturasSecaoAno.secoes],
  );
  const sectionBars: BarItem[] = site.aberturasSecaoAno.dados
    .filter((row) => row.ano === sectionYear)
    .sort((a, b) => b.aberturas - a.aberturas)
    .slice(0, 12)
    .map((row) => ({
      key: row.secao,
      label: `${row.secao} · ${sectionName.get(row.secao) ?? ""}`,
      value: row.aberturas,
      text: compact(row.aberturas),
      detail: `${row.secao} · ${sectionName.get(row.secao) ?? ""}: ${int(row.aberturas)}`,
    }));
  const ufBars: BarItem[] = site.aberturasUfAno.dados
    .filter((row) => row.ano === ufYear)
    .sort((a, b) => b.aberturas - a.aberturas)
    .slice(0, 12)
    .map((row) => ({
      key: ufLabel(row.uf),
      label: ufLabel(row.uf),
      value: row.aberturas,
      text: compact(row.aberturas),
      detail: `${ufLabel(row.uf)}: ${int(row.aberturas)}`,
    }));

  const recent = months.filter((row) => row.ano_mes >= `${(partialYear ?? 2026) - 10}-01`);
  const yearLabels = years.map((row) => String(row.ano));
  const recentLabels = recent.map((row) => row.ano_mes);

  return (
    <Row id="aberturas" title="Aberturas de estabelecimentos" hint="Início de atividade dos estabelecimentos que seguem no cadastro">
      <ChartCard
        title="Aberturas por ano"
        subtitle="Organizações políticas separadas: elas concentram picos em anos de eleição municipal"
        size="lg"
        chart={
          <LineChart
            label="Aberturas por ano, separando organizações políticas"
            labels={yearLabels}
            series={[
              { id: "demais", name: "Demais atividades", color: COLORS.primary, values: years.map((row) => row.abertos_demais) },
              { id: "org", name: "Organizações políticas", color: COLORS.secondary, values: years.map((row) => row.abertos_org_politica) },
            ]}
            format={(n) => int(n)}
            axisFormat={compact}
            partialLast={years.at(-1)?.parcial === true}
            endLabels
            tickIndexes={years.map((row, i) => (row.ano % 5 === 0 ? i : -1)).filter((i) => i >= 0)}
          />
        }
        table={{
          head: ["Ano", "Demais atividades", "Organizações políticas", "Total"],
          numeric: [1, 2, 3],
          rows: years.map((row) => [
            row.parcial ? `${row.ano} (parcial)` : row.ano,
            int(row.abertos_demais),
            int(row.abertos_org_politica),
            int(row.estabelecimentos_abertos),
          ]),
        }}
        footer="O último ano está incompleto: registros recentes ainda podem chegar ao cadastro."
      />
      <ChartCard
        title="Aberturas por mês"
        subtitle="Últimos dez anos"
        size="lg"
        chart={
          <LineChart
            label="Aberturas por mês nos últimos dez anos"
            labels={recentLabels}
            series={[
              { id: "demais", name: "Demais atividades", color: COLORS.primary, values: recent.map((row) => row.abertos_demais) },
              { id: "org", name: "Organizações políticas", color: COLORS.secondary, values: recent.map((row) => row.abertos_org_politica) },
            ]}
            format={(n) => int(n)}
            axisFormat={compact}
            partialLast={recent.at(-1)?.parcial === true}
            tickIndexes={monthTicks(recentLabels)}
            tickLabel={(label) => label.slice(0, 4)}
          />
        }
        table={{
          head: ["Mês", "Demais atividades", "Organizações políticas"],
          numeric: [1, 2],
          rows: recent.map((row) => [
            row.parcial ? `${row.ano_mes} (parcial)` : row.ano_mes,
            int(row.abertos_demais),
            int(row.abertos_org_politica),
          ]),
        }}
      />
      <ChartCard
        title="Aberturas por seção da CNAE"
        subtitle="As 12 maiores seções, sem organizações políticas"
        size="md"
        controls={<YearSelect label="Ano" value={sectionYear} years={sectionYears} partial={partialYear} onChange={setSectionYear} />}
        chart={<BarList items={sectionBars} label={`Aberturas por seção em ${sectionYear}`} />}
        footer={
          site.aberturasSecaoAno.celulas_suprimidas
            ? `${site.aberturasSecaoAno.celulas_suprimidas} recortes com menos de 5 unidades não são publicados.`
            : undefined
        }
      />
      <ChartCard
        title="Aberturas por UF"
        subtitle="As 12 maiores, sem organizações políticas"
        size="md"
        controls={<YearSelect label="Ano" value={ufYear} years={ufYears} partial={partialYear} onChange={setUfYear} />}
        chart={<BarList items={ufBars} label={`Aberturas por UF em ${ufYear}`} />}
      />
    </Row>
  );
}
