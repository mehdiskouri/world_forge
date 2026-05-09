/// <reference types="vitest" />
import { defineConfig } from "vitest/config";

// Phase 7 Stage F — bundle the popup canvas + connection-map frontend.
// `dist/` is force-included into the wheel by `pyproject.toml` and
// served by `forge_mcp.server.canvas_server.CanvasServer`.
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
    rollupOptions: {
      output: {
        // Hashed chunks: cache-bust between rebuilds.
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    globals: true,
  },
});
