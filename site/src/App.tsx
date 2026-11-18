import { useCallback, useEffect, useId, useState } from "react";
import { loadSite } from "./lib/data";
import { monthLabel } from "./lib/format";
import type { SiteData } from "./lib/types";
import { Agent } from "./sections/Agent";
import { Cities } from "./sections/Cities";
import { Evolution } from "./sections/Evolution";
import { Caveats, Footer } from "./sections/Footer";
import { Hero } from "./sections/Hero";
import { HowItWasMade } from "./sections/HowItWasMade";
import { Numbers } from "./sections/Numbers";
import { Openings } from "./sections/Openings";
import { Partners } from "./sections/Partners";
import { PipelineSection } from "./sections/PipelineSection";
import { Profile } from "./sections/Profile";
import { SimplesMei } from "./sections/SimplesMei";
import { States } from "./sections/States";

const LINKS: [string, string][] = [
  ["#panorama", "Em números"],
  ["#ufs", "Estados"],
  ["#evolucao", "Evolução"],
  ["#aberturas", "Aberturas"],
  ["#perfil", "Perfil"],
  ["#municipios", "Municípios"],
  ["#socios", "Sócios"],
  ["#como-foi-feito", "Como foi feito"],
  ["#pipeline", "Pipeline"],
  ["#agente", "Agente"],
];

type State =
  | { name: "loading" }
  | { name: "error"; message: string }
  | { name: "ready"; site: SiteData; switching: boolean; notice: string | null };

/** Mes pedido no endereco (?mes=2026-09); loadSite ignora o que nao estiver publicado. */
function requestedMonth(): string | null {
  const value = new URLSearchParams(window.location.search).get("mes");
  return value && /^\d{4}-\d{2}$/.test(value) ? value : null;
}

function rememberMonth(month: string, latest: string): void {
  const url = new URL(window.location.href);
  if (month === latest) url.searchParams.delete("mes");
  else url.searchParams.set("mes", month);
  window.history.replaceState(null, "", url);
}

function MonthSelect({ months, value, busy, onChange }: { months: string[]; value: string; busy: boolean; onChange: (month: string) => void }) {
  const id = useId();
  return (
    <label htmlFor={id} style={{ marginLeft: "auto", fontSize: "0.8rem", color: "var(--color-muted)", whiteSpace: "nowrap" }}>
      Mês{" "}
      <select id={id} className="field" value={value} disabled={busy} onChange={(event) => onChange(event.target.value)}>
        {months.map((month) => (
          <option key={month} value={month}>
            {monthLabel(month)}
          </option>
        ))}
      </select>
    </label>
  );
}

function TopBar({
  site,
  busy = false,
  onMonth,
}: {
  site?: SiteData;
  busy?: boolean;
  onMonth?: (month: string) => void;
}) {
  const month = site?.month;
  const links = LINKS.filter(([href]) => href !== "#evolucao" || site?.evolution);
  const [solid, setSolid] = useState(false);
  useEffect(() => {
    const onScroll = () => setSolid(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <div className="topbar" data-solid={solid}>
      <a className="wordmark" href="#inicio">
        CNPJ Pipeline
      </a>
      <nav className="nav" aria-label="Seções">
        {links.map(([href, label]) => (
          <a key={href} href={href}>
            {label}
          </a>
        ))}
      </nav>
      {site && month && site.months.length > 1 && onMonth ? (
        <MonthSelect months={site.months} value={month} busy={busy} onChange={onMonth} />
      ) : month ? (
        <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "var(--color-muted)", whiteSpace: "nowrap" }}>
          {monthLabel(month)}
        </span>
      ) : null}
    </div>
  );
}

export default function App() {
  const [state, setState] = useState<State>({ name: "loading" });
  useEffect(() => {
    let cancelled = false;
    loadSite(import.meta.env.BASE_URL, fetch, requestedMonth())
      .then((site) => {
        if (!cancelled) setState({ name: "ready", site, switching: false, notice: null });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ name: "error", message: error instanceof Error ? error.message : "erro desconhecido" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectMonth = useCallback(
    (month: string) => {
      if (state.name !== "ready" || month === state.site.month) return;
      const current = state.site;
      const latest = current.months[current.months.length - 1] ?? month;
      setState({ name: "ready", site: current, switching: true, notice: null });
      loadSite(import.meta.env.BASE_URL, fetch, month)
        .then((site) => {
          rememberMonth(site.month, latest);
          setState({ name: "ready", site, switching: false, notice: null });
        })
        .catch((error: unknown) => {
          const reason = error instanceof Error ? error.message : "erro desconhecido";
          setState({ name: "ready", site: current, switching: false, notice: `Não foi possível carregar ${monthLabel(month)}. ${reason}` });
        });
    },
    [state],
  );

  if (state.name !== "ready") {
    return (
      <>
        <TopBar />
        <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "6rem 1rem" }}>
          {state.name === "loading" ? (
            <p role="status" style={{ color: "var(--color-muted)" }}>
              Carregando os dados do mês de referência.
            </p>
          ) : (
            <div className="notice" role="alert" style={{ maxWidth: "36rem" }}>
              Não foi possível carregar os dados do site. {state.message}
            </div>
          )}
        </main>
      </>
    );
  }
  const { site } = state;
  return (
    <>
      <TopBar site={site} busy={state.switching} onMonth={selectMonth} />
      {state.notice ? (
        <div className="notice" role="alert" style={{ margin: "5rem auto 0", maxWidth: "36rem" }}>
          {state.notice}
        </div>
      ) : null}
      <Hero site={site} busy={state.switching} onMonth={selectMonth} />
      <main>
        <Numbers site={site} />
        <SimplesMei site={site} />
        <States site={site} />
        <Evolution site={site} />
        <Openings site={site} />
        <Profile site={site} />
        <Cities site={site} />
        <Partners site={site} />
        <HowItWasMade site={site} />
        <PipelineSection site={site} />
        <Agent site={site} />
        <Caveats site={site} />
      </main>
      <Footer site={site} />
    </>
  );
}
