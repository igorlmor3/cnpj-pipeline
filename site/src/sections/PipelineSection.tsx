import { ChartCard, DataTable, Poster, Row } from "../components/ui";
import { bytes, int, monthLabel, seconds } from "../lib/format";
import type { SiteData } from "../lib/types";

export function PipelineSection({ site }: { site: SiteData }) {
  const p = site.pipeline.dados;
  const q = p.qualidade;
  const label = monthLabel(site.month);
  const reconciled = p.gold.reconciliacoes.filter((r) => r.ok).length;
  return (
    <Row id="pipeline" title="O pipeline em números" hint="Raw, bronze, silver e gold. Os detalhes seguem o mês escolhido no topo">
      {site.runs.length > 1 ? (
        <ChartCard
          title="Mês a mês"
          subtitle={`${site.runs.length} meses processados, com as mesmas verificações em cada um`}
          size="lg"
          chart={
            <TableOnly
              label="Totais de cada mês processado"
              head={["Mês", "CSV", "Linhas lidas", "Rejeitadas", "Estabelecimentos", "Verificações"]}
              rows={site.runs.map((r) => [
                monthLabel(r.month),
                bytes(r.bytes_csv),
                int(r.linhas),
                int(r.linhas_rejeitadas),
                int(r.linhas_fato),
                `${int(r.verificacoes)} (${int(r.verificacoes_bloqueantes)} bloqueantes)`,
              ])}
              numeric={[1, 2, 3, 4, 5]}
            />
          }
          footer="Estabelecimentos: linhas do fato, com ativos e baixados. As verificações bloqueantes interrompem a execução se falham."
        />
      ) : null}
      <Poster size="sm">
        <h3 className="poster__title">Qualidade · {label}</h3>
        <p className="poster__sub">Verificações executadas sobre o mês completo</p>
        <div className="kpi__value" style={{ marginTop: "0.8rem" }}>
          {int(q.verificacoes)}
        </div>
        <div className="kpi__label">verificações</div>
        <div className="kpi__note">
          {int(q.verificacoes_bloqueantes)} bloqueantes e {int(q.avisos)} avisos. Em {seconds(q.segundos)}, com pico de{" "}
          {int(q.memoria_pico_mib)} MiB de memória (limite de {q.limite_memoria}).
        </div>
      </Poster>
      <Poster size="sm">
        <h3 className="poster__title">Verificações da gold · {label}</h3>
        <p className="poster__sub">Chaves únicas e somas conferidas entre as camadas</p>
        <div className="kpi__value" style={{ marginTop: "0.8rem" }}>
          {reconciled}/{p.gold.reconciliacoes.length}
        </div>
        <div className="kpi__label">passam</div>
        <div className="kpi__note">{int(p.bronze.linhas_rejeitadas)} linhas rejeitadas na bronze.</div>
      </Poster>
      <ChartCard
        title={`Bronze · ${label}`}
        subtitle="Tabelas lidas do CSV original e gravadas em Parquet"
        size="lg"
        chart={
          <TableOnly
            label="Tabelas da bronze"
            head={["Tabela", "Linhas", "CSV", "Parquet"]}
            rows={p.bronze.tabelas.map((t) => [t.nome, int(t.linhas), bytes(t.bytes_csv), bytes(t.bytes_parquet)])}
            numeric={[1, 2, 3]}
          />
        }
      />
      <ChartCard
        title={`Silver · ${label}`}
        subtitle="Tipagem, chaves e remoção de duplicadas"
        size="lg"
        chart={
          <TableOnly
            label="Tabelas da silver"
            head={["Tabela", "Entrada", "Saída", "Duplicadas", "Tempo"]}
            rows={p.silver.tabelas.map((t) => [
              t.nome,
              int(t.linhas_entrada),
              int(t.linhas_saida),
              int(t.duplicadas_removidas),
              seconds(t.segundos),
            ])}
            numeric={[1, 2, 3, 4]}
          />
        }
      />
      <ChartCard
        title={`Gold · ${label}`}
        subtitle="Modelos analíticos prontos para consulta"
        size="lg"
        chart={
          <TableOnly
            label="Modelos da gold"
            head={["Modelo", "Linhas", "Parquet", "Tempo"]}
            rows={p.gold.modelos.map((m) => [m.nome, int(m.linhas), bytes(m.bytes_parquet), seconds(m.segundos)])}
            numeric={[1, 2, 3]}
          />
        }
      />
      <ChartCard
        title={`Verificações da gold · ${label}`}
        subtitle="Unicidade das chaves e somas dos marts contra o fato e a silver"
        size="lg"
        chart={
          <TableOnly
            label="Verificações da gold"
            head={["Verificação", "Tipo", "Resultado"]}
            rows={p.gold.reconciliacoes.map((r) => [r.descricao, r.bloqueante ? "Bloqueante" : "Informativa", r.ok ? "ok" : "falhou"])}
          />
        }
      />
    </Row>
  );
}

function TableOnly({
  label,
  head,
  rows,
  numeric,
}: {
  label: string;
  head: string[];
  rows: (string | number)[][];
  numeric?: number[];
}) {
  return <DataTable spec={{ head, rows, numeric }} label={label} />;
}
