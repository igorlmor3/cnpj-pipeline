import { Poster, Row } from "../components/ui";
import { bytes, compact, int, monthLabel } from "../lib/format";
import type { SiteData } from "../lib/types";

interface Step {
  title: string;
  text: string[];
  metric: string;
  caption: string;
}

export function buildSteps(site: SiteData): Step[] {
  const p = site.pipeline.dados;
  const models = p.gold.modelos;
  const dims = models.filter((m) => m.nome.startsWith("dim_")).length;
  const marts = models.filter((m) => m.nome.startsWith("mart_")).length;
  const facts = models.filter((m) => m.nome.startsWith("fato_")).length;
  const silverOut = p.silver.tabelas.reduce((sum, t) => sum + t.linhas_saida, 0);
  const silverDuplicates = p.silver.tabelas.reduce((sum, t) => sum + t.duplicadas_removidas, 0);
  const months = site.months;
  const first = months[0] ?? site.month;
  const last = months[months.length - 1] ?? site.month;
  const evolution = site.evolution;
  const comparisons = evolution?.dados.movimentos.length ?? 0;
  const newTotal = evolution?.dados.movimentos.reduce((sum, m) => sum + m.novos, 0) ?? 0;
  const orchestration: Step = {
    title: "Orquestração",
    text: [
      "O fluxo roda no Airflow, em um DAG mensal que escolhe entre dados reais e amostra sintética, usa a qualidade como porta de entrada da publicação e deixa o envio à AWS opcional.",
      "Sem o Airflow, um comando roda vários meses em sequência, mostra o tempo de cada etapa e, se algo falha, imprime o comando para retomar do ponto em que parou.",
    ],
    metric: "Airflow",
    caption: "DAG com 11 tarefas; 40 a 50 min de processamento por mês real, fora o download",
  };
  const evolutionStep: Step[] = evolution
    ? [
        {
          title: "Evolução",
          text: [
            "Cada mês é uma foto completa do cadastro. Comparar duas fotos, CNPJ a CNPJ, separa os CNPJs novos, os que sumiram e as mudanças de situação, e mede a defasagem entre o início da atividade e a foto em que o CNPJ aparece.",
            "Só contagens saem do cálculo: recortes com menos de 5 estabelecimentos são omitidos, e a variação de ativos de cada mês é conferida contra a diferença dos estoques.",
          ],
          metric: compact(newTotal),
          caption: `CNPJs novos em ${comparisons} comparações entre meses`,
        },
      ]
    : [];
  return [
    {
      title: "Download",
      text: [
        `A Receita Federal publica o cadastro todo mês em arquivos .zip de CSV: Empresas, Estabelecimentos e Sócios, divididos em várias partes, mais as tabelas de domínio (CNAE, municípios, naturezas e outras). O pipeline já processou ${months.length} meses, de ${monthLabel(first)} a ${monthLabel(last)}.`,
        "O download retoma de onde parou, tenta de novo com espera crescente quando o servidor derruba a conexão e grava um manifesto com o tamanho e o SHA-256 de cada arquivo. Um arquivo que não começa como zip é recusado: em um dos meses o firewall da Receita devolveu páginas de recusa no lugar de dois arquivos, e hoje isso é detectado e repetido.",
      ],
      metric: bytes(p.bronze.bytes_csv),
      caption: `de CSV em ${p.bronze.tabelas.length} tabelas, em ${monthLabel(site.month)}`,
    },
    {
      title: "Bronze",
      text: [
        "O CSV (latin-1, separador ponto e vírgula, sem cabeçalho) vira Parquet em UTF-8, em streaming, com tudo ainda como texto.",
        "Linhas quebradas não somem em silêncio: vão para uma pasta de rejeitados, com o número da linha e o motivo. Se a fração passar do limite, o arquivo falha.",
      ],
      metric: compact(p.bronze.linhas),
      caption: `linhas lidas, ${int(p.bronze.linhas_rejeitadas)} rejeitadas`,
    },
    {
      title: "Silver",
      text: [
        "Datas, decimais e indicadores ganham tipo. Datas inexistentes e valores que significam não informado viram nulo. Chaves inválidas e duplicadas vão para uma quarentena, com o motivo.",
        "Entre duplicadas, fica a linha mais preenchida, com desempate pelo hash do conteúdo: a mesma entrada dá a mesma saída, em qualquer ordem.",
      ],
      metric: compact(silverOut),
      caption: `linhas na saída, ${int(silverDuplicates)} duplicada removida`,
    },
    {
      title: "Gold",
      text: [
        `${models.length} modelos em SQL do DuckDB: ${dims} dimensões, ${facts} fato com uma linha por estabelecimento e ${marts} marts agregados.`,
        "O fato não tem razão social, endereço, telefone, e-mail nem nomes de sócios. É a única base de onde saem o site e o agente.",
      ],
      metric: `${models.length} modelos`,
      caption: `${p.gold.reconciliacoes.filter((r) => r.ok).length} de ${p.gold.reconciliacoes.length} verificações da gold passam`,
    },
    {
      title: "Qualidade",
      text: [
        "Verificações rodam sobre o mês completo: formato e dígitos verificadores do CNPJ (módulo 11), domínios de códigos, datas incoerentes, unicidade de chaves e contagens iguais entre as camadas.",
        "As bloqueantes derrubam o comando e indicam que a gold não deve ser publicada.",
      ],
      metric: int(p.qualidade.verificacoes),
      caption: `verificações, ${int(p.qualidade.verificacoes_bloqueantes)} bloqueantes`,
    },
    orchestration,
    ...evolutionStep,
    {
      title: "AWS",
      text: [
        "A gold vai para um bucket S3 privado e criptografado, e o Athena consulta as tabelas pelo Glue, com limite de dados lidos por consulta.",
        "Tudo é descrito em Terraform, com usuário IAM de menor privilégio e alerta de orçamento. O envio é idempotente: arquivos idênticos são pulados.",
      ],
      metric: "S3, Glue, Athena",
      caption: "consultas em SQL sobre a gold na nuvem",
    },
    {
      title: "Agente",
      text: [
        "O modelo recebe o esquema da gold e escreve uma consulta. Uma guarda só deixa passar SELECT, só sobre as tabelas permitidas e com o mês fixado pelo programa. Se a consulta falhar, o erro volta ao modelo para uma correção.",
        "A avaliação compara o resultado da consulta com o de uma consulta de referência, num conjunto reservado que o prompt nunca viu.",
      ],
      metric: "24 de 25",
      caption: "respostas certas no conjunto reservado, na primeira execução",
    },
    {
      title: "API e site",
      text: [
        "A API do agente é uma função na AWS com limite de perguntas por visitante e por dia, teto de gasto e uma chave de desligamento.",
        "O site é estático e lê só arquivos JSON agregados, sem recortes com menos de 5 unidades. As respostas de exemplo foram gravadas com o modelo real, então aparecem sem custo por visita.",
      ],
      metric: "Estático",
      caption: "o site é um build no GitHub Pages, sem servidor próprio",
    },
  ];
}

const DECISIONS: { title: string; text: string }[] = [
  {
    title: "Privacidade por construção",
    text: "A gold não tem razão social, endereço, telefone, e-mail nem nomes de sócios, e um teste confere que nada disso aparece nos arquivos publicados. Tudo que o site e o agente mostram é agregado.",
  },
  {
    title: "Falhar alto, não em silêncio",
    text: "Linhas quebradas vão para rejeitados e linhas inválidas para quarentena, sempre com o motivo. Se a fração passa do limite, a etapa falha e a saída é removida em vez de seguir com dado ruim.",
  },
  {
    title: "Reprodutível",
    text: "A deduplicação é determinística e a publicação na AWS é idempotente, comparando tamanho e SHA-256. Rodar de novo dá o mesmo resultado.",
  },
  {
    title: "Custo controlado",
    text: "Alerta de orçamento na AWS, limite de dados lidos por consulta no Athena, sem NAT Gateway, teto de gasto no provedor do modelo e uma chave que desliga o agente.",
  },
  {
    title: "Medir antes de afirmar",
    text: "O agente é avaliado pelo resultado da consulta, não pelo texto do SQL. O conjunto reservado existe para o ajuste do prompt não inflar a nota, e os limites da medição estão no README.",
  },
  {
    title: "Ressalvas à vista",
    text: "Cada arquivo publicado carrega as suas ressalvas, como a foto de um único mês, os picos das organizações políticas e os recortes pequenos omitidos. O site as mostra, em vez de esconder.",
  },
];

const TOOLS = [
  "Python",
  "DuckDB",
  "Airflow",
  "Docker",
  "Parquet",
  "sqlglot",
  "Terraform",
  "S3",
  "Glue",
  "Athena",
  "Lambda",
  "DynamoDB",
  "Claude Haiku 5.5",
  "React",
  "Vite",
  "Tailwind",
  "Vitest",
  "GitHub Actions",
];

const DOCS: [string, string][] = [
  ["README.md", "Visão geral, resultados medidos e roadmap"],
  ["docs/architecture.md", "Decisões de arquitetura e alternativas consideradas"],
  ["docs/agent_design.md", "Desenho do agente e suas defesas"],
  ["docs/data_quality_rules.md", "Catálogo de problemas de qualidade dos dados"],
  ["docs/site_data.md", "Arquivos JSON do site e o que os torna seguros para publicar"],
];

export function HowItWasMade({ site }: { site: SiteData }) {
  const steps = buildSteps(site);
  const repo = site.config.repoUrl.replace(/\/+$/, "");
  return (
    <>
      <Row id="como-foi-feito" title="Como foi feito" hint="Do arquivo da Receita ao agente">
        {steps.map((step, i) => (
          <Poster key={step.title} size="md">
            <span className="tag">Etapa {i + 1}</span>
            <h3 className="poster__title" style={{ marginTop: "0.5rem", fontSize: "1.35rem" }}>
              {step.title}
            </h3>
            <div className="answer" style={{ marginTop: "0.7rem" }}>
              {step.text.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </div>
            <div style={{ marginTop: "0.9rem", borderTop: "1px solid var(--color-line)", paddingTop: "0.8rem" }}>
              <div className="kpi__value" style={{ fontSize: "1.6rem" }}>
                {step.metric}
              </div>
              <div className="kpi__note">{step.caption}</div>
            </div>
          </Poster>
        ))}
      </Row>
      <Row title="Decisões de engenharia" hint="O que guiou as escolhas">
        {DECISIONS.map((decision) => (
          <Poster key={decision.title} size="sm">
            <h3 className="poster__title" style={{ fontSize: "1.1rem" }}>
              {decision.title}
            </h3>
            <p className="poster__sub" style={{ marginTop: "0.6rem", fontSize: "0.9rem", lineHeight: 1.5, color: "var(--color-ink2)" }}>
              {decision.text}
            </p>
          </Poster>
        ))}
      </Row>
      <Row title="Ferramentas e código" hint="Tudo está versionado no repositório">
        <Poster size="md">
          <h3 className="poster__title">Ferramentas</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.45rem", marginTop: "0.8rem" }}>
            {TOOLS.map((tool) => (
              <span key={tool} className="badge">
                {tool}
              </span>
            ))}
          </div>
        </Poster>
        <Poster size="md">
          <h3 className="poster__title">Leia no repositório</h3>
          <ul style={{ margin: "0.8rem 0 0", padding: 0, listStyle: "none", display: "grid", gap: "0.6rem" }}>
            {DOCS.map(([path, description]) => (
              <li key={path}>
                <a href={`${repo}/blob/main/${path}`} target="_blank" rel="noreferrer" style={{ color: "#ffffff", fontWeight: 600 }}>
                  {path}
                </a>
                <div className="poster__sub">{description}</div>
              </li>
            ))}
          </ul>
        </Poster>
      </Row>
    </>
  );
}
