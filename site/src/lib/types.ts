export interface Envelope<T> {
  arquivo: string;
  esquema: number;
  mes_referencia: string;
  titulo: string;
  descricao: string;
  ressalvas: Record<string, string>;
  celulas_suprimidas?: number;
  dados: T;
}

export interface Overview {
  estabelecimentos_ativos: number;
  empresas_ativas_org_politica: number;
  todas: Simples;
  sem_org_politica: Simples;
  estabelecimentos_no_cadastro: number;
  aberturas_org_politica: number;
  aberturas_antes_do_inicio_da_serie: number;
  total_socios: number;
  ufs: number;
  maior_uf: string;
  participacao_maior_uf_pct: number;
  participacao_cinco_maiores_ufs_pct: number;
}

export interface Simples {
  empresas_ativas: number;
  optantes_simples: number;
  optantes_mei: number;
  pct_simples: number;
  pct_mei: number;
}

export interface UfRow {
  uf: string | null;
  uf_nome: string | null;
  estabelecimentos_ativos: number;
  matrizes_ativas: number;
  empresas_ativas: number;
  empresas_ativas_org_politica: number;
  optantes_simples: number;
  optantes_mei: number;
  pct_simples: number;
  pct_mei: number;
}

export interface AberturaAno {
  ano: number;
  estabelecimentos_abertos: number;
  abertos_org_politica: number;
  abertos_demais: number;
  parcial: boolean;
}

export interface AberturaMes {
  ano_mes: string;
  estabelecimentos_abertos: number;
  abertos_org_politica: number;
  abertos_demais: number;
  parcial: boolean;
}

export interface Secao {
  codigo: string;
  descricao: string;
}

export interface AberturasSecaoAno {
  secoes: Secao[];
  dados: { ano: number; secao: string; aberturas: number }[];
  ano_parcial?: number;
}

export interface AberturasUfAno {
  dados: { ano: number; uf: string | null; aberturas: number }[];
  ano_parcial?: number;
}

export interface AtivosSecao {
  secao: string;
  secao_descricao: string;
  estabelecimentos_ativos: number;
  matrizes_ativas: number;
  optantes_simples: number;
  optantes_mei: number | null;
}

export interface MunicipioTop {
  uf: string | null;
  municipio_nome: string | null;
  estabelecimentos_ativos: number;
  matrizes_ativas: number;
  posicao: number;
}

export interface PerfilPorte {
  porte: string | null;
  porte_descricao: string | null;
  empresas_ativas: number;
  participacao_pct: number;
}

export interface PerfilNatureza {
  grupos: { grupo: string | null; empresas_ativas: number; participacao_pct: number }[];
  outras: { empresas_ativas: number; participacao_pct: number };
  dados: {
    natureza: string;
    natureza_descricao: string;
    natureza_grupo: string | null;
    empresas_ativas: number;
    participacao_pct: number;
  }[];
}

export interface CapitalMediano {
  porte: string;
  porte_descricao: string;
  natureza: string;
  natureza_descricao: string;
  empresas: number;
  capital_social_mediano: number;
}

export interface Socios {
  tipos: { codigo: string; descricao: string; socios: number }[];
  dados: { qualificacao: string; descricao: string; tipo: string; socios: number }[];
}

export interface Pipeline {
  gold: {
    modelos: { nome: string; linhas: number; bytes_parquet: number; segundos: number }[];
    reconciliacoes: { nome: string; descricao: string; ok: boolean; bloqueante: boolean }[];
  };
  bronze: {
    linhas: number;
    linhas_rejeitadas: number;
    bytes_csv: number;
    bytes_parquet: number;
    tabelas: { nome: string; linhas: number; bytes_csv: number; bytes_parquet: number }[];
  };
  silver: {
    tabelas: {
      nome: string;
      linhas_entrada: number;
      linhas_saida: number;
      chaves_invalidas: number;
      duplicadas_removidas: number;
      segundos: number;
    }[];
  };
  qualidade: {
    verificacoes: number;
    verificacoes_bloqueantes: number;
    avisos: number;
    amostra: boolean;
    segundos: number;
    memoria_pico_mib: number;
    limite_memoria: string;
  };
}

export interface AgentExample {
  id: string;
  pergunta: string;
  status: string;
  resposta: string;
  sql?: string;
  colunas?: string[];
  linhas?: (string | number | null)[][];
  truncado?: boolean;
}

export interface AgentExamples {
  esquema: number;
  mes_referencia: string;
  modelo: string;
  exemplos: AgentExample[];
}

export interface SiteConfig {
  apiUrl: string;
  repoUrl: string;
}

export interface EvolucaoSerie {
  mes: string;
  estabelecimentos_ativos: number;
  empresas_ativas: number;
  optantes_simples: number;
  optantes_mei: number;
  pct_simples: number;
  pct_mei: number;
}

export interface EvolucaoMovimento {
  mes: string;
  mes_anterior: string;
  novos: number;
  novos_ativos: number;
  removidos: number;
  removidos_ativos: number;
  mudaram_de_situacao: number;
  passaram_a_ativos: number;
  deixaram_de_ser_ativos: number;
  saldo_ativos: number;
}

export interface EvolucaoTransicao {
  mes: string;
  de: string;
  para: string;
  de_descricao: string;
  para_descricao: string;
  estabelecimentos: number;
}

export interface EvolucaoDefasagem {
  mes: string;
  faixa: string;
  faixa_descricao: string;
  novos: number;
}

export interface EvolucaoSaldoUf {
  mes: string;
  uf: string | null;
  uf_nome: string | null;
  saldo: number;
}

export interface Evolucao {
  arquivo: string;
  esquema: number;
  titulo: string;
  descricao: string;
  ressalvas: Record<string, string>;
  meses: string[];
  celulas_suprimidas?: number;
  dados: {
    serie: EvolucaoSerie[];
    movimentos: EvolucaoMovimento[];
    transicoes: EvolucaoTransicao[];
    defasagem: EvolucaoDefasagem[];
    saldo_uf: EvolucaoSaldoUf[];
  };
}

export interface SiteData {
  month: string;
  /** Meses publicados, do mais antigo ao mais recente. */
  months: string[];
  source: string;
  overview: Envelope<Overview>;
  uf: Envelope<UfRow[]>;
  aberturasAno: Envelope<AberturaAno[]>;
  aberturasMes: Envelope<AberturaMes[]>;
  aberturasSecaoAno: Envelope<AberturasSecaoAno["dados"]> & { secoes: Secao[]; ano_parcial?: number };
  aberturasUfAno: Envelope<AberturasUfAno["dados"]> & { ano_parcial?: number };
  ativosSecao: Envelope<AtivosSecao[]>;
  municipios: Envelope<MunicipioTop[]>;
  porte: Envelope<PerfilPorte[]>;
  natureza: Envelope<PerfilNatureza["dados"]> & {
    grupos: PerfilNatureza["grupos"];
    outras: PerfilNatureza["outras"];
  };
  capital: Envelope<CapitalMediano[]>;
  socios: Envelope<Socios["dados"]> & { tipos: Socios["tipos"] };
  pipeline: Envelope<Pipeline>;
  examples: AgentExamples | null;
  evolution: Evolucao | null;
  config: SiteConfig;
}
