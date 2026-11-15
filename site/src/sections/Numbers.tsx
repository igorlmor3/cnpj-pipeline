import { bytes, compact, int, pct } from "../lib/format";
import type { SiteData } from "../lib/types";
import { Kpi, Row } from "../components/ui";

export function Numbers({ site }: { site: SiteData }) {
  const o = site.overview.dados;
  const p = site.pipeline.dados;
  const reduction = p.bronze.bytes_csv > 0 ? (1 - p.bronze.bytes_parquet / p.bronze.bytes_csv) * 100 : 0;
  return (
    <Row id="panorama" title="Em números" hint="Foto do cadastro no mês de referência">
      <Kpi
        value={compact(o.todas.empresas_ativas)}
        label="Empresas ativas"
        note={`Inclui ${int(o.empresas_ativas_org_politica)} organizações políticas (CNAE 9492800).`}
      />
      <Kpi
        value={compact(o.estabelecimentos_ativos)}
        label="Estabelecimentos ativos"
        note={`De ${compact(o.estabelecimentos_no_cadastro)} no cadastro, entre ativos e baixados.`}
      />
      <Kpi value={pct(o.todas.pct_simples)} label="Optantes pelo Simples" note={`${int(o.todas.optantes_simples)} empresas ativas, em um regime tributário simplificado para micro e pequenas empresas.`} />
      <Kpi value={pct(o.todas.pct_mei)} label="São MEI" note={`${int(o.todas.optantes_mei)} empresas ativas. O MEI é uma categoria dentro do Simples Nacional.`} />
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
