import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api so that the browser sees one origin in development and
// in production alike. Same-origin in both places means no CORS surprises at deploy.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly. Vite otherwise binds [::1] only on Windows, so anything
    // addressing 127.0.0.1 (curl, a health probe, some test runners) fails to connect
    // while a browser on "localhost" works — a confusing way to lose half an hour.
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
