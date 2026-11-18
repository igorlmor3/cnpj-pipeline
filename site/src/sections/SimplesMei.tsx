import { int, monthLabel, pct } from "../lib/format";
import { stockAt } from "../lib/series";
import type { SiteData } from "../lib/types";

export interface SimplesFacts {
  optantesSimples: number;
  optantesMei: number;
  pctSimples: number;
  pctMei: number;
  pctForaDoSimples: number;
  pctMeiDentroDoSimples: number;
}

export function simplesFacts(site: SiteData): SimplesFacts {
  const t = site.overview.dados.todas;
  return {
    optantesSimples: t.optantes_simples,
    optantesMei: t.optantes_mei,
    pctSimples: t.pct_simples,
    pctMei: t.pct_mei,
    pctForaDoSimples: Math.max(0, 100 - t.pct_simples),
    pctMeiDentroDoSimples: t.optantes_simples > 0 ? (t.optantes_mei / t.optantes_simples) * 100 : 0,
  };
}

export function SimplesMei({ site }: { site: SiteData }) {
  const f = simplesFacts(site);
  const first = site.months[0] ?? site.month;
  const base = first !== site.month ? stockAt(site, first) : null;
  const history = (value: number | undefined) => (base && value !== undefined ? ` Em ${monthLabel(first)} eram ${pct(value)}.` : "");
  return (
    <section id="simples-mei" className="explain" aria-labelledby="simples-mei-title">
      <div className="row__head">
        <h2 id="simples-mei-title">O que são Simples e MEI</h2>
        <p>Dois termos que aparecem nos números e nos estados</p>
      </div>
      <div className="explain__grid">
        <article className="explain__card">
          <span className="tag">Regime tributário</span>
          <h3>Simples Nacional</h3>
          <p>
            É um regime de tributação simplificado para microempresas e empresas de pequeno porte. Em vez de calcular e pagar cada imposto
            separadamente, a empresa reúne vários tributos federais, estaduais e municipais em uma única guia mensal, com alíquota que depende
            da atividade e do faturamento.
          </p>
          <p>
            No cadastro da Receita Federal, cada empresa informa se é optante. Quem não aparece na tabela do Simples é contado aqui como não
            optante, e costuma estar no Lucro Presumido ou no Lucro Real.
          </p>
          <div className="explain__stat">
            <strong>{pct(f.pctSimples)}</strong>
            <span>
              das empresas ativas são optantes ({int(f.optantesSimples)} empresas).{history(base?.pct_simples)}
            </span>
          </div>
        </article>
        <article className="explain__card">
          <span className="tag">Categoria de empresa</span>
          <h3>MEI, Microempreendedor Individual</h3>
          <p>
            É a menor forma de empresa formal do país, pensada para quem trabalha por conta própria. O faturamento anual tem um limite fixado em
            lei, o empreendedor pode ter no máximo um empregado e não pode ser sócio ou titular de outra empresa.
          </p>
          <p>
            O MEI paga um valor mensal fixo em vez de percentuais sobre o faturamento e recebe um CNPJ próprio. Todo MEI é optante pelo Simples
            Nacional: o MEI é uma categoria dentro do Simples, não um regime à parte.
          </p>
          <div className="explain__stat">
            <strong>{pct(f.pctMei)}</strong>
            <span>
              das empresas ativas são MEI ({int(f.optantesMei)} empresas).{history(base?.pct_mei)}
            </span>
          </div>
        </article>
        <article className="explain__card">
          <span className="tag">Como ler os percentuais</span>
          <h3>Os dois se sobrepõem</h3>
          <p>
            Por isso os percentuais do site não se somam: {pct(f.pctSimples)} de optantes pelo Simples já inclui os {pct(f.pctMei)} de MEI.
          </p>
          <p>
            Dentro do Simples, {pct(f.pctMeiDentroDoSimples)} das empresas são MEI. Fora do Simples ficam {pct(f.pctForaDoSimples)} das empresas ativas.
            Em cada estado, os percentuais são calculados sobre as empresas ativas daquela UF.
          </p>
          <div className="explain__stat">
            <strong>{pct(f.pctMeiDentroDoSimples)}</strong>
            <span>dos optantes pelo Simples são MEI</span>
          </div>
        </article>
      </div>
    </section>
  );
}
