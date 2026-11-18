import type { AgentExamples, Envelope, Evolucao, MonthRun, Pipeline, SiteConfig, SiteData } from "./types";

type Fetcher = typeof fetch;

const FILES = {
  overview: "overview.json",
  uf: "uf.json",
  aberturasAno: "aberturas_ano.json",
  aberturasMes: "aberturas_mes.json",
  aberturasSecaoAno: "aberturas_secao_ano.json",
  aberturasUfAno: "aberturas_uf_ano.json",
  ativosSecao: "ativos_secao.json",
  municipios: "municipios_top.json",
  porte: "perfil_porte.json",
  natureza: "perfil_natureza.json",
  capital: "capital_mediano.json",
  socios: "socios.json",
  pipeline: "pipeline.json",
} as const;

export const DEFAULT_CONFIG: SiteConfig = {
  apiUrl: "",
  repoUrl: "https://github.com/igorlmor3/cnpj-pipeline",
};

async function getJson<T>(fetcher: Fetcher, url: string): Promise<T> {
  const response = await fetcher(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

async function getOptional<T>(fetcher: Fetcher, url: string): Promise<T | null> {
  try {
    const response = await fetcher(url);
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

function checkEnvelope(name: string, value: Envelope<unknown>, month: string): void {
  if (value.esquema !== 1) throw new Error(`${name}: esquema ${String(value.esquema)} nao suportado`);
  if (value.mes_referencia !== month) {
    throw new Error(`${name}: mes ${String(value.mes_referencia)} diferente de ${month}`);
  }
  if (value.dados === undefined || value.dados === null) throw new Error(`${name}: sem dados`);
}

function checkConfig(value: Partial<SiteConfig> | null): SiteConfig {
  const apiUrl = typeof value?.apiUrl === "string" && /^https:\/\//.test(value.apiUrl) ? value.apiUrl : "";
  const repoUrl =
    typeof value?.repoUrl === "string" && /^https:\/\//.test(value.repoUrl) ? value.repoUrl : DEFAULT_CONFIG.repoUrl;
  return { apiUrl, repoUrl };
}

/** Totais de um mes a partir do pipeline.json; null quando o arquivo falta ou nao tem o formato esperado. */
function summarizeRun(month: string, value: Partial<Envelope<Partial<Pipeline>>> | null): MonthRun | null {
  const d = value?.dados;
  if (!value || value.esquema !== 1 || value.mes_referencia !== month || !d?.bronze || !d.qualidade) return null;
  const numbers = [d.bronze.linhas, d.bronze.linhas_rejeitadas, d.bronze.bytes_csv, d.qualidade.verificacoes, d.qualidade.verificacoes_bloqueantes];
  if (!numbers.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  return {
    month,
    linhas: d.bronze.linhas,
    linhas_rejeitadas: d.bronze.linhas_rejeitadas,
    bytes_csv: d.bronze.bytes_csv,
    verificacoes: d.qualidade.verificacoes,
    verificacoes_bloqueantes: d.qualidade.verificacoes_bloqueantes,
  };
}

const MONTH = /^\d{4}-\d{2}$/;

/** Aceita a serie de evolucao so quando o formato e o esquema batem; qualquer outra coisa vira null. */
function checkEvolution(value: Partial<Evolucao> | null): Evolucao | null {
  if (!value || value.esquema !== 1 || !Array.isArray(value.meses) || !value.dados) return null;
  const d = value.dados;
  const lists = [d.serie, d.movimentos, d.transicoes, d.defasagem, d.saldo_uf];
  if (!lists.every(Array.isArray)) return null;
  return value as Evolucao;
}

/**
 * Carrega um mes listado em months.json (por padrao o mais recente; um mes que nao esta na lista
 * e ignorado) e valida o envelope de cada arquivo. A serie de evolucao e opcional.
 */
export async function loadSite(base: string, fetcher: Fetcher = fetch, requested?: string | null): Promise<SiteData> {
  const root = base.endsWith("/") ? base : `${base}/`;
  const index = await getJson<{ esquema: number; ultimo: string; meses?: string[] }>(fetcher, `${root}data/months.json`);
  if (index.esquema !== 1 || !MONTH.test(index.ultimo)) throw new Error("months.json invalido");
  const listed = (Array.isArray(index.meses) ? index.meses : []).filter((m) => MONTH.test(m));
  const months = [...new Set([...listed, index.ultimo])].sort();
  const month = requested && months.includes(requested) ? requested : index.ultimo;
  const dir = `${root}data/${month}/`;

  const keys = Object.keys(FILES) as (keyof typeof FILES)[];
  const otherMonths = months.filter((m) => m !== month);
  const [monthIndex, config, examples, evolution, otherPipelines, ...loaded] = await Promise.all([
    getJson<{ fonte: string; mes_referencia: string }>(fetcher, `${dir}index.json`),
    getOptional<Partial<SiteConfig>>(fetcher, `${root}config.json`),
    getOptional<AgentExamples>(fetcher, `${dir}agent_examples.json`),
    getOptional<Partial<Evolucao>>(fetcher, `${root}data/evolucao.json`),
    Promise.all(otherMonths.map((m) => getOptional<Partial<Envelope<Partial<Pipeline>>>>(fetcher, `${root}data/${m}/pipeline.json`))),
    ...keys.map((key) => getJson<Envelope<unknown>>(fetcher, `${dir}${FILES[key]}`)),
  ]);

  const result: Record<string, Envelope<unknown>> = {};
  keys.forEach((key, i) => {
    const envelope = loaded[i] as Envelope<unknown>;
    checkEnvelope(FILES[key], envelope, month);
    result[key] = envelope;
  });

  const validExamples =
    examples && examples.esquema === 1 && examples.mes_referencia === month && Array.isArray(examples.exemplos)
      ? examples
      : null;

  const runs = [
    ...otherMonths.map((m, i) => summarizeRun(m, otherPipelines[i] ?? null)),
    summarizeRun(month, result.pipeline as Envelope<Pipeline>),
  ]
    .filter((run): run is MonthRun => run !== null)
    .sort((a, b) => a.month.localeCompare(b.month));

  return {
    month,
    months,
    source: monthIndex.fonte,
    ...(result as unknown as Omit<SiteData, "month" | "months" | "source" | "examples" | "evolution" | "runs" | "config">),
    examples: validExamples,
    evolution: checkEvolution(evolution),
    runs,
    config: checkConfig(config),
  };
}

/** Junta as ressalvas de todos os arquivos (codigo -> texto), sem repetir. */
export function collectCaveats(site: SiteData): { code: string; text: string }[] {
  const seen = new Map<string, string>();
  const envelopes = [
    site.overview,
    site.uf,
    site.aberturasAno,
    site.aberturasMes,
    site.aberturasSecaoAno,
    site.aberturasUfAno,
    site.ativosSecao,
    site.municipios,
    site.porte,
    site.natureza,
    site.capital,
    site.socios,
    site.pipeline,
  ] as Envelope<unknown>[];
  if (site.evolution) envelopes.push({ ressalvas: site.evolution.ressalvas } as Envelope<unknown>);
  for (const envelope of envelopes) {
    for (const [code, text] of Object.entries(envelope.ressalvas ?? {})) {
      if (!seen.has(code)) seen.set(code, text);
    }
  }
  return [...seen].map(([code, text]) => ({ code, text }));
}
