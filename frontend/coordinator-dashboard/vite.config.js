/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/coordinator": "http://localhost:8000",
      "/parse": "http://localhost:8000",
    },
  },
  // Vitest config: risk.js is a pure module, so the default Node environment
  // is sufficient (no DOM needed). Run with `npm test`.
  test: {
    environment: "node",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
