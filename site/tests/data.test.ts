import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { collectCaveats, loadSite } from "../src/lib/data";
import { publicFetch, readPublic } from "./helpers";

const MONTH_DIR = join(__dirname, "..", "public", "data", "2026-10");

describe("loadSite com os arquivos publicados", () => {
  it("carrega o mes mais recente e todos os arquivos", async () => {
    const site = await loadSite("/cnpj-pipeline/", publicFetch());
    expect(site.month).toBe("2026-10");
    expect(site.source).toContain("Receita Federal");
    expect(site.uf.dados.length).toBeGreaterThan(20);
    expect(site.overview.dados.todas.empresas_ativas).toBeGreaterThan(0);
    expect(site.config.apiUrl).toMatch(/^https:\/\//);
  });

  it("o mes de referencia e o esquema de cada arquivo batem com o index", async () => {
    const index = readPublic<{ arquivos: { arquivo: string }[] }>("data/2026-10/index.json");
    for (const { arquivo } of index.arquivos) {
      const file = readPublic<{ esquema: number; mes_referencia: string }>(`data/2026-10/${arquivo}`);
      expect(file.esquema).toBe(1);
      expect(file.mes_referencia).toBe("2026-10");
    }
  });

  it("os arquivos publicados nao carregam campos de identificacao nem horarios", () => {
    const names = readdirSync(MONTH_DIR).filter((n) => n.endsWith(".json"));
    for (const name of names) {
      const text = readFileSync(join(MONTH_DIR, name), "utf-8");
      expect(text, name).not.toMatch(/cnpj_basico|razao_social|nome_fantasia|logradouro|"email"|telefone|gerado_em|timestamp/i);
    }
  });

  it("falha com mensagem clara quando um arquivo muda de esquema", async () => {
    const bad = { ...readPublic<object>("data/2026-10/uf.json"), esquema: 2 };
    await expect(loadSite("/cnpj-pipeline/", publicFetch({ "data/2026-10/uf.json": bad }))).rejects.toThrow(/uf\.json.*esquema/);
  });

  it("falha quando o mes do arquivo difere do mes mais recente", async () => {
    const bad = { ...readPublic<object>("data/2026-10/overview.json"), mes_referencia: "2026-09" };
    await expect(loadSite("/cnpj-pipeline/", publicFetch({ "data/2026-10/overview.json": bad }))).rejects.toThrow(/mes/);
  });

  it("falha quando um arquivo obrigatorio nao existe", async () => {
    await expect(loadSite("/cnpj-pipeline/", publicFetch({ "data/2026-10/socios.json": null }))).rejects.toThrow(/socios\.json/);
  });

  it("segue sem as respostas gravadas e sem a configuracao", async () => {
    const site = await loadSite("/cnpj-pipeline/", publicFetch({ "config.json": null }));
    expect(site.config.apiUrl).toBe("");
    expect(site.config.repoUrl).toContain("github.com");
  });

  it("ignora respostas gravadas de outro mes ou com formato errado", async () => {
    const stale = { esquema: 1, mes_referencia: "2026-09", modelo: "x", exemplos: [] };
    expect((await loadSite("/", publicFetch({ "data/2026-10/agent_examples.json": stale }))).examples).toBeNull();
    const broken = { esquema: 1, mes_referencia: "2026-10", modelo: "x", exemplos: "nao e lista" };
    expect((await loadSite("/", publicFetch({ "data/2026-10/agent_examples.json": broken }))).examples).toBeNull();
  });

  it("aceita respostas gravadas do mes", async () => {
    const good = { esquema: 1, mes_referencia: "2026-10", modelo: "x", exemplos: [{ id: "a", pergunta: "q", status: "answered", resposta: "r" }] };
    const site = await loadSite("/", publicFetch({ "data/2026-10/agent_examples.json": good }));
    expect(site.examples?.exemplos).toHaveLength(1);
  });

  it("so aceita enderecos https na configuracao", async () => {
    const site = await loadSite("/", publicFetch({ "config.json": { apiUrl: "http://inseguro.test", repoUrl: "javascript:alert(1)" } }));
    expect(site.config.apiUrl).toBe("");
    expect(site.config.repoUrl).toBe("https://github.com/igorlmor3/cnpj-pipeline");
  });

  it("junta as ressalvas sem repetir codigos", async () => {
    const site = await loadSite("/", publicFetch());
    const caveats = collectCaveats(site);
    const codes = caveats.map((c) => c.code);
    expect(new Set(codes).size).toBe(codes.length);
    expect(codes).toContain("org_politica");
    expect(codes).toContain("mes_parcial");
  });
});
