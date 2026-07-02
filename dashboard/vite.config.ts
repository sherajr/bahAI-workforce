import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// All dashboard requests go to /api/... and are proxied to the FastAPI server
// on port 8765 (the /api prefix is stripped). This includes /api/outputs/<file>
// for generated bookmark images, so no CORS configuration is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8765",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
