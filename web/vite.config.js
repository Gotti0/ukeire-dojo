import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버에서는 /api를 Python 서버(ukeire_dojo.server)로 넘긴다. 빌드한 dist는 그 서버가 직접 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
