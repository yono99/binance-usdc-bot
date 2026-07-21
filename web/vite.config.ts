import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base absolute "/" — wajib untuk BrowserRouter (/trade, /agent, …).
// dev: proxy /api -> dashboard FastAPI (port 8000); SPA fallback bawaan Vite.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
