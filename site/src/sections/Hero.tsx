import { compact, monthLabel, pct } from "../lib/format";
import type { SiteData } from "../lib/types";
import { Icon } from "../components/ui";

/** Silhueta decorativa da serie anual de aberturas, desenhada ao fundo do destaque. */
function Backdrop({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const w = 1000;
  const h = 400;
  const points = values.map((v, i) => [(i * w) / Math.max(1, values.length - 1), h - (v / max) * h * 0.8 - 20] as const);
  const line = points.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
  return (
    <svg className="hero__art" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={`${line} L${w} ${h} L0 ${h} Z`} fill="#e50914" fillOpacity={0.12} />
      <path d={line} fill="none" stroke="#e50914" strokeOpacity={0.55} strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Hero({ site }: { site: SiteData }) {
  const o = site.overview.dados;
  const series = site.aberturasAno.dados.map((row) => row.abertos_demais);
  return (
    <header className="hero" id="inicio">
      <Backdrop values={series} />
      <div className="hero__body">
        <span className="tag">Dados abertos do CNPJ · {monthLabel(site.month)}</span>
        <h1>O cadastro de empresas do Brasil, camada por camada</h1>
        <p>
          Um pipeline de ponta a ponta sobre os dados abertos da Receita Federal: {compact(o.estabelecimentos_no_cadastro, true)}{" "}
          de estabelecimentos viram tabelas confiáveis, verificadas e consultáveis por SQL, inclusive em linguagem natural.
        </p>
        <div className="hero__stats">
          <div>
            <strong>{compact(o.todas.empresas_ativas, true)}</strong>
            <span>empresas ativas</span>
          </div>
          <div>
            <strong>{pct(o.todas.pct_mei)}</strong>
            <span>são MEI</span>
          </div>
          <div>
            <strong>{pct(o.participacao_maior_uf_pct)}</strong>
            <span>estão em {o.maior_uf}</span>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.7rem", marginTop: "1.6rem" }}>
          <a className="btn btn--light" href="#agente">
            {Icon.play} Perguntar ao agente
          </a>
          <a className="btn btn--grey" href="#como-foi-feito">
            {Icon.info} Como foi feito
          </a>
        </div>
      </div>
    </header>
  );
}
