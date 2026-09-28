import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard runs as a dev-server app: Vite serves the UI on :5173 and proxies
// /api to the Python process, so iterating on the interface never needs the server
// restarted and never needs a rebuild.
//
// `vite build` writes into examples/server/dist, which the Python process will
// serve instead of its "not built yet" page.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
