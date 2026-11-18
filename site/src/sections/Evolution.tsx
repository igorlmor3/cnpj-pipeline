import { type BarItem, BarList, COLORS } from "../components/charts";
import { ChartCard, Kpi, Poster, Row } from "../components/ui";
import { compact, int, monthLabel, pct, signed, signedCompact, ufLabel } from "../lib/format";
import type { Evolucao, EvolucaoMovimento, EvolucaoSerie, SiteData } from "../lib/types";

const TOP_TRANSITIONS = 8;
const TOP_UFS = 6;

export interface EvolutionFacts {
  month: string;
  /** Movimentos entre a foto anterior e a deste mes; null no primeiro mes da serie. */
  movement: EvolucaoMovimento | null;
  stock: EvolucaoSerie | null;
  transitions: { label: string; value: number }[];
  lag: { label: string; value: number; share: number }[];
  ufGain: { uf: string; label: string; value: number }[];
  ufLoss: { uf: string; label: string; value: number }[];
}

export function evolutionFacts(evolution: Evolucao, month: string): EvolutionFacts {
  const d = evolution.dados;
  const movement = d.movimentos.find((row) => row.mes === month) ?? null;
  const stock = d.serie.find((row) => row.mes === month) ?? null;
  const transitions = d.transicoes
    .filter((row) => row.mes === month)
    .sort((a, b) => b.estabelecimentos - a.estabelecimentos)
    .slice(0, TOP_TRANSITIONS)
    .map((row) => ({ label: `${row.de_descricao} para ${row.para_descricao}`, value: row.estabelecimentos }));
  const lagRows = d.defasagem.filter((row) => row.mes === month);
  const lagTotal = lagRows.reduce((sum, row) => sum + row.novos, 0);
  const lag = lagRows.map((row) => ({
    label: row.faixa_descricao,
    value: row.novos,
    share: lagTotal > 0 ? (row.novos / lagTotal) * 100 : 0,
  }));
  const ufRows = d.saldo_uf
    .filter((row) => row.mes === month)
    .map((row) => ({ uf: ufLabel(row.uf), label: row.uf_nome ?? ufLabel(row.uf), value: row.saldo }));
  const ufGain = ufRows
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, TOP_UFS);
  const ufLoss = ufRows
    .filter((row) => row.value < 0)
    .sort((a, b) => a.value - b.value)
    .slice(0, TOP_UFS);
  return { month, movement, stock, transitions, lag, ufGain, ufLoss };
}

function signedBars(rows: { key: string; label: string; value: number }[], suffix: string): BarItem[] {
  return rows.map((row) => ({
    key: row.key,
    label: row.label,
    value: Math.abs(row.value),
    text: signedCompact(row.value),
    detail: `${row.label}: ${signed(row.value)} ${suffix}`,
    color: row.value < 0 ? COLORS.secondary : COLORS.primary,
  }));
}

export function Evolution({ site }: { site: SiteData }) {
  const evolution = site.evolution;
  if (!evolution || evolution.dados.serie.length < 2) return null;
  const facts = evolutionFacts(evolution, site.month);
  const { movimentos, serie } = evolution.dados;
  const m = facts.movement;
  const previous = m ? monthLabel(m.mes_anterior) : null;

  const balanceBars: BarItem[] = movimentos.map((row) => ({
    key: row.mes,
    label: monthLabel(row.mes),
    value: Math.abs(row.saldo_ativos),
    text: signedCompact(row.saldo_ativos),
    detail: `${monthLabel(row.mes_anterior)} para ${monthLabel(row.mes)}: ${signed(row.saldo_ativos)} estabelecimentos ativos`,
    color: row.saldo_ativos < 0 ? COLORS.secondary : COLORS.primary,
  }));
  const balanceTable = {
    head: ["Mês", "Ativos na foto", "Variação"],
    rows: serie.map((row) => {
      const move = movimentos.find((item) => item.mes === row.mes);
      return [monthLabel(row.mes), int(row.estabelecimentos_ativos), move ? signed(move.saldo_ativos) : "primeira foto"];
    }),
    numeric: [1, 2],
  };

  return (
    <Row
      id="evolucao"
      title="Evolução entre meses"
      hint="Fotos mensais completas do cadastro, comparadas CNPJ a CNPJ. Só contagens, nenhum CNPJ é publicado."
    >
      {facts.stock ? (
        <Kpi
          value={compact(facts.stock.estabelecimentos_ativos)}
          label={`Estabelecimentos ativos em ${monthLabel(site.month)}`}
          note={`${int(facts.stock.estabelecimentos_ativos)} na foto do mês.`}
        />
      ) : null}
      {m ? (
        <>
          <Kpi
            value={signedCompact(m.saldo_ativos)}
            label={`Variação líquida de ativos desde ${previous}`}
            note={`${signed(m.saldo_ativos)} estabelecimentos entre as duas fotos.`}
          />
          <Kpi
            value={compact(m.novos_ativos)}
            label="CNPJs novos e ativos"
            note={`${int(m.novos)} estabelecimentos aparecem na foto de ${monthLabel(m.mes)} sem existir na de ${previous}.`}
          />
          <Kpi
            value={compact(m.deixaram_de_ser_ativos)}
            label="Deixaram de ser ativos"
            note={`Já existiam e mudaram de situação (inaptos, baixados, suspensos). ${int(m.passaram_a_ativos)} fizeram o caminho inverso.`}
          />
        </>
      ) : (
        <Poster size="md">
          <h3 className="poster__title">Primeiro mês da série</h3>
          <p className="poster__sub">
            Os movimentos comparam duas fotos. Como {monthLabel(site.month)} é a primeira, não há foto anterior; escolha um mês mais recente
            no seletor do topo.
          </p>
        </Poster>
      )}
      <ChartCard
        title="Variação líquida de ativos por mês"
        subtitle="Estabelecimentos ativos a mais (ou a menos) na foto do mês em relação à anterior"
        size="lg"
        chart={<BarList items={balanceBars} label="Variação líquida de estabelecimentos ativos por mês" />}
        table={balanceTable}
        footer="Vermelho: alta. Azul: queda. Entradas e saídas misturam abertura de CNPJs com mudanças de situação cadastral."
      />
      {m ? (
        <>
          <ChartCard
            title={`Quem entra e quem sai dos ativos em ${monthLabel(m.mes)}`}
            subtitle={`Entre as fotos de ${previous} e ${monthLabel(m.mes)}`}
            size="lg"
            chart={
              <BarList
                label="Entradas e saídas de estabelecimentos ativos"
                items={[
                  { key: "novos", label: "Novos e ativos", value: m.novos_ativos, text: compact(m.novos_ativos), color: COLORS.primary },
                  { key: "volta", label: "Passaram a ativos", value: m.passaram_a_ativos, text: compact(m.passaram_a_ativos), color: COLORS.primary },
                  { key: "saida", label: "Saíram dos ativos", value: m.deixaram_de_ser_ativos, text: compact(m.deixaram_de_ser_ativos), color: COLORS.secondary },
                  { key: "sumiram", label: "Sumiram do cadastro", value: m.removidos, text: compact(m.removidos), color: COLORS.secondary },
                ]}
              />
            }
            footer="Os CNPJs baixados continuam nas fotos da Receita: por isso nada some, só muda de situação."
          />
          <ChartCard
            title="Mudanças de situação cadastral"
            subtitle={`As ${TOP_TRANSITIONS} transições mais comuns em ${monthLabel(m.mes)}`}
            size="lg"
            chart={
              <BarList
                label="Transições de situação cadastral"
                items={facts.transitions.map((row) => ({
                  key: row.label,
                  label: row.label,
                  value: row.value,
                  text: compact(row.value),
                  detail: `${row.label}: ${int(row.value)}`,
                }))}
              />
            }
            table={{
              head: ["De, para", "Estabelecimentos"],
              rows: facts.transitions.map((row) => [row.label, int(row.value)]),
              numeric: [1],
            }}
          />
          <ChartCard
            title="Quando os CNPJs novos começaram a atuar"
            subtitle="Meses entre o início da atividade registrado e a foto em que o CNPJ aparece"
            size="md"
            chart={
              <BarList
                label="Defasagem dos CNPJs novos"
                items={facts.lag.map((row) => ({
                  key: row.label,
                  label: row.label,
                  value: row.value,
                  text: `${compact(row.value)} · ${pct(row.share)}`,
                  detail: `${row.label}: ${int(row.value)} (${pct(row.share)})`,
                }))}
              />
            }
            footer="Valores altos indicam cadastros registrados com atraso, e não aberturas do mês."
          />
          <ChartCard
            title="Variação líquida de ativos por estado"
            subtitle={`Maiores altas e quedas em ${monthLabel(m.mes)}`}
            size="lg"
            chart={
              <BarList
                label="Variação líquida de estabelecimentos ativos por UF"
                items={signedBars(
                  [...facts.ufGain, ...facts.ufLoss].map((row) => ({ key: row.uf, label: `${row.uf} · ${row.label}`, value: row.value })),
                  "estabelecimentos ativos",
                )}
              />
            }
            footer="Vermelho: alta. Azul: queda. Os estados não listados variaram menos."
          />
        </>
      ) : null}
    </Row>
  );
}
