import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base relatif agar aset termuat saat di-serve FastAPI di "/".
// dev: proxy /api -> dashboard FastAPI (port 8000).
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
