import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import JavaScriptObfuscator from "javascript-obfuscator";
import { fileURLToPath, URL } from "node:url";
import type { Plugin } from "vite";

const sourceDirectory = fileURLToPath(new URL("./src/", import.meta.url)).replaceAll("\\", "/");

function obfuscateApplicationChunks(): Plugin {
  return {
    name: "obfuscate-application-chunks",
    apply: "build",
    enforce: "post",
    renderChunk(code, chunk) {
      const isApplicationChunk = Object.keys(chunk.modules).some((moduleId) =>
        moduleId.replaceAll("\\", "/").startsWith(sourceDirectory),
      );
      if (!isApplicationChunk) return null;

      const obfuscated = JavaScriptObfuscator.obfuscate(code, {
        compact: true,
        controlFlowFlattening: false,
        deadCodeInjection: false,
        identifierNamesGenerator: "hexadecimal",
        ignoreImports: true,
        renameGlobals: false,
        seed: 1337,
        selfDefending: false,
        stringArray: true,
        stringArrayRotate: true,
        stringArrayShuffle: true,
        stringArrayThreshold: 0.5,
        unicodeEscapeSequence: false,
      });
      return { code: obfuscated.getObfuscatedCode(), map: null };
    },
  };
}

const productionPlugins =
  process.env.OBFUSCATE === "false" ? [react()] : [react(), obfuscateApplicationChunks()];

// В dev-режиме фронтенд ходит в API через прокси Vite: браузер видит один
// origin (localhost:5173), поэтому cookie и CSRF работают как в проде, без
// послаблений CORS. В production статику отдаёт Caddy, а /api он же
// проксирует в контейнер API.
export default defineConfig({
  plugins: productionPlugins,
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(moduleId) {
          if (!moduleId.includes("node_modules")) return undefined;
          if (moduleId.includes("react")) return "react";
          return "vendor";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["tests/e2e/**", "node_modules/**", "dist/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/{api,auth,App,components,hooks}.{ts,tsx}"],
      thresholds: {
        lines: 60,
        functions: 60,
        branches: 60,
        statements: 60,
      },
    },
  },
});
