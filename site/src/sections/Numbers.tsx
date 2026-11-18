import { bytes, compact, int, monthLabel, pct, signedCompact, signedPp } from "../lib/format";
import { previousMonth, stockAt } from "../lib/series";
import type { SiteData } from "../lib/types";
import { Kpi, Row } from "../components/ui";

export function Numbers({ site }: { site: SiteData }) {
  const o = site.overview.dados;
  const p = site.pipeline.dados;
  const prev = previousMonth(site);
  const now = stockAt(site, site.month);
  const before = stockAt(site, prev);
  const since = prev ? monthLabel(prev) : "";
  const change = (a: number | undefined, b: number | undefined) => (a !== undefined && b !== undefined ? a - b : null);
  const companies = change(now?.empresas_ativas, before?.empresas_ativas);
  const sites = change(now?.estabelecimentos_ativos, before?.estabelecimentos_ativos);
  const simples = change(now?.pct_simples, before?.pct_simples);
  const mei = change(now?.pct_mei, before?.pct_mei);
  const reduction = p.bronze.bytes_csv > 0 ? (1 - p.bronze.bytes_parquet / p.bronze.bytes_csv) * 100 : 0;
  return (
    <Row id="panorama" title="Em números" hint={`Foto do cadastro em ${monthLabel(site.month)}${prev ? `, com a variação desde ${since}` : ""}`}>
      <Kpi
        value={compact(o.todas.empresas_ativas)}
        label="Empresas ativas"
        note={`Inclui ${int(o.empresas_ativas_org_politica)} organizações políticas (CNAE 9492800).${companies !== null ? ` ${signedCompact(companies)} desde ${since}.` : ""}`}
      />
      <Kpi
        value={compact(o.estabelecimentos_ativos)}
        label="Estabelecimentos ativos"
        note={`De ${compact(o.estabelecimentos_no_cadastro)} no cadastro, entre ativos e baixados.${sites !== null ? ` ${signedCompact(sites)} ativos desde ${since}.` : ""}`}
      />
      <Kpi value={pct(o.todas.pct_simples)} label="Optantes pelo Simples" note={`${int(o.todas.optantes_simples)} empresas ativas, em um regime tributário simplificado para micro e pequenas empresas.${simples !== null ? ` ${signedPp(simples)} desde ${since}.` : ""}`} />
      <Kpi value={pct(o.todas.pct_mei)} label="São MEI" note={`${int(o.todas.optantes_mei)} empresas ativas. O MEI é uma categoria dentro do Simples Nacional.${mei !== null ? ` ${signedPp(mei)} desde ${since}.` : ""}`} />
      <Kpi value={compact(o.total_socios)} label="Vínculos de sócios" note="Só contagens: nenhum nome de pessoa é publicado." />
      <Kpi
        value={pct(o.participacao_cinco_maiores_ufs_pct)}
        label="Nas cinco maiores UFs"
        note={`${o.maior_uf} sozinha reúne ${pct(o.participacao_maior_uf_pct)} das empresas ativas.`}
      />
      <Kpi
        value={compact(p.bronze.linhas)}
        label="Linhas processadas"
        note={`${bytes(p.bronze.bytes_csv)} de CSV viram ${bytes(p.bronze.bytes_parquet)} em Parquet (${pct(reduction, 0)} menos).`}
      />
    </Row>
  );
}
