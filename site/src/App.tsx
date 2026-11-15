import { useEffect, useState } from "react";
import { loadSite } from "./lib/data";
import { monthLabel } from "./lib/format";
import type { SiteData } from "./lib/types";
import { Agent } from "./sections/Agent";
import { Cities } from "./sections/Cities";
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
  ["#aberturas", "Aberturas"],
  ["#perfil", "Perfil"],
  ["#municipios", "Municípios"],
  ["#socios", "Sócios"],
  ["#como-foi-feito", "Como foi feito"],
  ["#pipeline", "Pipeline"],
  ["#agente", "Agente"],
];

type State = { name: "loading" } | { name: "error"; message: string } | { name: "ready"; site: SiteData };

function TopBar({ month }: { month?: string }) {
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
        {LINKS.map(([href, label]) => (
          <a key={href} href={href}>
            {label}
          </a>
        ))}
      </nav>
      {month ? (
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
    loadSite(import.meta.env.BASE_URL)
      .then((site) => {
        if (!cancelled) setState({ name: "ready", site });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ name: "error", message: error instanceof Error ? error.message : "erro desconhecido" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
      <TopBar month={site.month} />
      <Hero site={site} />
      <main>
        <Numbers site={site} />
        <SimplesMei site={site} />
        <States site={site} />
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
