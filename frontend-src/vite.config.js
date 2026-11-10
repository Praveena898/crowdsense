import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// base "/static/" because FastAPI serves the build folder under /static
export default defineConfig({ plugins: [react()], base: "/static/",
  server: { proxy: { "/api": "http://127.0.0.1:8000" } } });
