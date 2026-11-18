import { collectCaveats } from "../lib/data";
import { monthLabel } from "../lib/format";
import { spanLabel } from "../lib/series";
import type { SiteData } from "../lib/types";

export function Caveats({ site }: { site: SiteData }) {
  const caveats = collectCaveats(site);
  return (
    <section id="ressalvas" className="row" aria-labelledby="ressalvas-titulo">
      <div className="row__head">
        <h2 id="ressalvas-titulo">Antes de interpretar</h2>
      </div>
      <ul style={{ margin: "1rem 0 0", padding: "0 var(--gutter)", maxWidth: "58rem", display: "grid", gap: "0.7rem", listStyle: "none" }}>
        {caveats.map((c) => (
          <li key={c.code} className="notice">
            {c.text}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Footer({ site }: { site: SiteData }) {
  return (
    <footer className="footer">
      <p style={{ margin: 0 }}>
        Fonte: {site.source}, foto de {monthLabel(site.month)}
        {site.months.length > 1 ? ` (série de ${spanLabel(site)})` : ""}. Todos os números são agregados; recortes com menos de 5 unidades não são publicados.
      </p>
      <p style={{ margin: "0.6rem 0 0" }}>
        Código, testes e documentação em{" "}
        <a href={site.config.repoUrl} rel="noreferrer">
          {site.config.repoUrl.replace("https://", "")}
        </a>
        .
      </p>
    </footer>
  );
}
