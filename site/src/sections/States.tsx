import { type BarItem, BarList, COLORS } from "../components/charts";
import { ChartCard, DataTable, Row } from "../components/ui";
import { compact, int, monthLabel, pct, signedCompact, ufLabel } from "../lib/format";
import { previousMonth } from "../lib/series";
import type { SiteData, UfRow } from "../lib/types";

function TopNumber({ rank }: { rank: number }) {
  const text = String(rank);
  const width = text.length === 1 ? 92 : 160;
  return (
    <svg className="top10__num" viewBox={`0 0 ${width} 154`} aria-hidden="true">
      <text
        x={width / 2}
        y={140}
        textAnchor="middle"
        fontSize={150}
        fontWeight={800}
        fill="#141414"
        stroke="#6b6b6b"
        strokeWidth={4}
        paintOrder="stroke"
        letterSpacing={text.length === 1 ? 0 : -14}
      >
        {text}
      </text>
    </svg>
  );
}

export function topUfs(rows: UfRow[], size = 10): UfRow[] {
  return [...rows].sort((a, b) => b.empresas_ativas - a.empresas_ativas).slice(0, size);
}

export function States({ site }: { site: SiteData }) {
  const rows = site.uf.dados;
  const prev = previousMonth(site);
  const before = new Map(
    (site.evolution?.dados.serie_uf ?? []).filter((row) => row.mes === prev).map((row) => [ufLabel(row.uf), row.empresas_ativas]),
  );
  const ranked = topUfs(rows);
  const bySimples = [...rows].sort((a, b) => b.pct_simples - a.pct_simples);
  const byMei = [...rows].sort((a, b) => b.pct_mei - a.pct_mei);
  const toBars = (list: UfRow[], key: "pct_simples" | "pct_mei", color: string): BarItem[] =>
    list.map((row) => ({
      key: ufLabel(row.uf),
      label: `${ufLabel(row.uf)}${row.uf_nome ? ` · ${row.uf_nome}` : ""}`,
      value: row[key],
      text: pct(row[key]),
      color,
    }));
  const table = {
    head: ["UF", "Empresas ativas", "Estabelecimentos", "Simples", "MEI"],
    numeric: [1, 2, 3, 4],
    rows: [...rows]
      .sort((a, b) => b.empresas_ativas - a.empresas_ativas)
      .map((row) => [
        row.uf_nome ? `${ufLabel(row.uf)} · ${row.uf_nome}` : ufLabel(row.uf),
        int(row.empresas_ativas),
        int(row.estabelecimentos_ativos),
        pct(row.pct_simples),
        pct(row.pct_mei),
      ]),
  };
  return (
    <>
      <Row
        id="ufs"
        title="Top 10 estados em empresas ativas"
        hint={`Ordem por empresas ativas em ${monthLabel(site.month)}${prev ? `; a variação é desde ${monthLabel(prev)}` : ""}`}
      >
        {ranked.map((row, i) => (
          <div className="top10" key={ufLabel(row.uf)}>
            <TopNumber rank={i + 1} />
            <div className="top10__card">
              <div className="top10__uf">{ufLabel(row.uf)}</div>
              <div className="top10__name">{row.uf_nome ?? ""}</div>
              <div className="top10__val">{compact(row.empresas_ativas, true)}</div>
              {before.has(ufLabel(row.uf)) ? (
                <div className="top10__name">{signedCompact(row.empresas_ativas - (before.get(ufLabel(row.uf)) ?? 0))} empresas</div>
              ) : null}
            </div>
          </div>
        ))}
      </Row>
      <Row title="Estados em detalhe" hint="Simples e MEI entre as empresas ativas de cada UF">
        <ChartCard
          title="Optantes pelo Simples"
          subtitle="Parcela das empresas ativas, por UF"
          chart={<BarList items={toBars(bySimples, "pct_simples", COLORS.primary)} label="Optantes pelo Simples por UF" max={100} />}
          footer="A barra vai de 0 a 100%."
        />
        <ChartCard
          title="Microempreendedores individuais"
          subtitle="Parcela das empresas ativas, por UF"
          chart={<BarList items={toBars(byMei, "pct_mei", COLORS.secondary)} label="MEI por UF" max={100} />}
          footer="A barra vai de 0 a 100%."
        />
        <ChartCard
          title="Todas as UFs"
          subtitle="Empresas e estabelecimentos ativos"
          size="lg"
          chart={<DataTable spec={table} label="Todas as UFs" />}
        />
      </Row>
    </>
  );
}
