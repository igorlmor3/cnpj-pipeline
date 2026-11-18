import { useEffect, useRef, useState } from "react";
import { AnswerView } from "../components/Answer";
import { Icon, Poster, Row } from "../components/ui";
import { type AskOutcome, MAX_QUESTION_CHARS, askAgent } from "../lib/api";
import { monthLabel } from "../lib/format";
import { spanLabel } from "../lib/series";
import type { AgentExample, SiteData } from "../lib/types";

function Modal({ example, onClose }: { example: AgentExample; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [onClose]);
  return (
    <div
      className="modal"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal__panel" role="dialog" aria-modal="true" aria-label="Pergunta e resposta do agente">
        <button type="button" className="modal__close" ref={closeRef} onClick={onClose} aria-label="Fechar">
          {Icon.close}
        </button>
        <span className="tag">Resposta gravada</span>
        <h3 style={{ margin: "0.5rem 2.5rem 1rem 0", fontSize: "1.4rem" }}>{example.pergunta}</h3>
        <AnswerView answer={example} />
      </div>
    </div>
  );
}

type Phase = { name: "idle" } | { name: "loading" } | { name: "done"; outcome: AskOutcome };

function LiveAgent({ apiUrl, suggestions }: { apiUrl: string; suggestions: string[] }) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const submit = async () => {
    if (phase.name === "loading") return;
    controller.current?.abort();
    controller.current = new AbortController();
    setPhase({ name: "loading" });
    try {
      const outcome = await askAgent(apiUrl, question, fetch, controller.current.signal);
      setPhase({ name: "done", outcome });
    } catch {
      // pedido cancelado ao sair da pagina: nada a mostrar
    }
  };

  const tooLong = question.trim().length > MAX_QUESTION_CHARS;
  return (
    <div className="agent">
      <h3 style={{ margin: "0 0 0.4rem", fontSize: "1.25rem" }}>Pergunte você mesmo</h3>
      <p style={{ margin: "0 0 0.9rem", color: "var(--color-muted)", fontSize: "0.9rem" }}>
        O agente escreve uma consulta SQL sobre as tabelas agregadas da gold, executa e explica o resultado. Ele não consulta dados de pessoas nem de uma empresa
        específica. Há limites de uso por visitante e por dia.
      </p>
      <label htmlFor="pergunta" style={{ position: "absolute", left: "-9999px" }}>
        Pergunta sobre o cadastro de CNPJ
      </label>
      <textarea
        id="pergunta"
        value={question}
        maxLength={MAX_QUESTION_CHARS + 50}
        onChange={(event) => setQuestion(event.target.value)}
        placeholder="Ex.: Quais UFs têm a maior parcela de MEI?"
      />
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.6rem", margin: "0.6rem 0" }}>
        <button type="button" className="btn btn--red" onClick={submit} disabled={phase.name === "loading" || !question.trim() || tooLong}>
          {Icon.play} {phase.name === "loading" ? "Consultando" : "Perguntar"}
        </button>
        <span style={{ fontSize: "0.8rem", color: tooLong ? "#ffffff" : "var(--color-muted)" }}>
          {question.trim().length}/{MAX_QUESTION_CHARS}
        </span>
        {suggestions.map((s) => (
          <button type="button" key={s} className="chip" onClick={() => setQuestion(s)}>
            {s}
          </button>
        ))}
      </div>
      <div aria-live="polite">
        {phase.name === "loading" ? <p className="notice">Consultando a gold. Isso leva alguns segundos.</p> : null}
        {phase.name === "done" ? <Outcome outcome={phase.outcome} /> : null}
      </div>
    </div>
  );
}

function Outcome({ outcome }: { outcome: AskOutcome }) {
  if (outcome.kind === "answered") {
    return (
      <Poster size="lg">
        <AnswerView answer={outcome.answer} />
      </Poster>
    );
  }
  return <p className="notice">{outcome.message}</p>;
}

export function Agent({ site }: { site: SiteData }) {
  const [open, setOpen] = useState<AgentExample | null>(null);
  const examples = site.examples?.exemplos ?? [];
  const apiUrl = site.config.apiUrl;
  return (
    <>
      {examples.length > 0 ? (
        <Row id="agente" title="Pergunte ao agente" hint="Respostas gravadas com o modelo real, sem custo de uso">
          {examples.map((example) => (
            <button
              type="button"
              key={example.id}
              className="poster poster--sm"
              style={{ textAlign: "left", color: "inherit", font: "inherit", cursor: "pointer", minHeight: "10rem" }}
              onClick={() => setOpen(example)}
              aria-haspopup="dialog"
            >
              <span className="tag">{example.status === "answered" ? "Com consulta SQL" : "Fora do escopo"}</span>
              <h3 className="poster__title" style={{ marginTop: "0.6rem", fontSize: "1.1rem", lineHeight: 1.3 }}>
                {example.pergunta}
              </h3>
              <p className="poster__sub" style={{ marginTop: "0.6rem" }}>
                Ver resposta
              </p>
            </button>
          ))}
        </Row>
      ) : (
        <section id="agente" className="row">
          <div className="row__head">
            <h2>Pergunte ao agente</h2>
          </div>
        </section>
      )}
      {site.months.length > 1 ? (
        <div className="agent" style={{ marginBottom: "1rem" }}>
          <p className="notice">
            O agente consulta todos os meses publicados ({spanLabel(site)}). Sem citar um mês, ele usa o mais recente, {monthLabel(site.agentMonth)}.
            Para comparar, cite os meses na pergunta. O mês escolhido no topo da página não muda o que ele consulta. Ele compara fotos do cadastro, e a
            diferença entre dois meses mistura aberturas, baixas e mudanças de situação.
          </p>
        </div>
      ) : null}
      {examples.length > 0 ? (
        <div className="agent" style={{ marginBottom: "1.5rem" }}>
          <p className="notice">
            As respostas do agente excluem as organizações políticas (CNAE 9492800) das contagens de empresas e dizem isso no texto. Por isso os totais
            podem diferir em algumas unidades dos gráficos acima, que incluem esse grupo nas empresas ativas.
          </p>
        </div>
      ) : null}
      {apiUrl ? (
        <LiveAgent apiUrl={apiUrl} suggestions={examples.filter((e) => e.status === "answered").slice(0, 3).map((e) => e.pergunta)} />
      ) : (
        <div className="agent">
          <p className="notice">O agente ao vivo não está conectado a esta versão do site. As respostas gravadas continuam disponíveis.</p>
        </div>
      )}
      {open ? <Modal example={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}
