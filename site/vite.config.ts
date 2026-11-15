import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// No GitHub Pages o site fica em /<repositorio>/. Para outro endereco, defina BASE_PATH no build.
export default defineConfig({
  base: process.env.BASE_PATH ?? "/cnpj-pipeline/",
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    globals: true,
    css: false,
  },
});
