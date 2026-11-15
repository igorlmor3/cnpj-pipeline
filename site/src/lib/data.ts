import type { AgentExamples, Envelope, SiteConfig, SiteData } from "./types";

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

/** Carrega o mes mais recente listado em months.json e valida o envelope de cada arquivo. */
export async function loadSite(base: string, fetcher: Fetcher = fetch): Promise<SiteData> {
  const root = base.endsWith("/") ? base : `${base}/`;
  const months = await getJson<{ esquema: number; ultimo: string }>(fetcher, `${root}data/months.json`);
  if (months.esquema !== 1 || !/^\d{4}-\d{2}$/.test(months.ultimo)) throw new Error("months.json invalido");
  const month = months.ultimo;
  const dir = `${root}data/${month}/`;

  const keys = Object.keys(FILES) as (keyof typeof FILES)[];
  const [index, config, examples, ...loaded] = await Promise.all([
    getJson<{ fonte: string; mes_referencia: string }>(fetcher, `${dir}index.json`),
    getOptional<Partial<SiteConfig>>(fetcher, `${root}config.json`),
    getOptional<AgentExamples>(fetcher, `${dir}agent_examples.json`),
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

  return {
    month,
    source: index.fonte,
    ...(result as unknown as Omit<SiteData, "month" | "source" | "examples" | "config">),
    examples: validExamples,
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
  for (const envelope of envelopes) {
    for (const [code, text] of Object.entries(envelope.ressalvas ?? {})) {
      if (!seen.has(code)) seen.set(code, text);
    }
  }
  return [...seen].map(([code, text]) => ({ code, text }));
}
