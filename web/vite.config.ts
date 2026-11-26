/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    // Page tests render whole Ant Design tables and open tooltips; CI runners can take over 5 s
    // (Vitest's default) for one of them, so they get headroom rather than flaking.
    testTimeout: 20_000
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true
      },
      "/healthz": {
        target: "http://localhost:8000",
        changeOrigin: true
      }
    }
  }
});
