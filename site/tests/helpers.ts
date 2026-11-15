import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const PUBLIC = join(__dirname, "..", "public");

/** fetch que serve os arquivos reais de public/ (os mesmos que a pagina publicada entrega). */
export function publicFetch(overrides: Record<string, unknown> = {}): typeof fetch {
  return ((input: RequestInfo | URL) => {
    const url = String(input);
    const path = url.replace(/^https?:\/\/[^/]+/, "").replace(/^\/cnpj-pipeline\//, "").replace(/^\//, "");
    if (path in overrides) {
      const value = overrides[path];
      if (value === null) return Promise.resolve(new Response("nao encontrado", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify(value), { status: 200 }));
    }
    const file = join(PUBLIC, path);
    if (!existsSync(file)) return Promise.resolve(new Response("nao encontrado", { status: 404 }));
    return Promise.resolve(new Response(readFileSync(file, "utf-8"), { status: 200 }));
  }) as typeof fetch;
}

export function readPublic<T>(path: string): T {
  return JSON.parse(readFileSync(join(PUBLIC, path), "utf-8")) as T;
}
