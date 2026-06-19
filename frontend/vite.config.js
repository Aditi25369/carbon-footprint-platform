import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Carbon Nexus — Vite config
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        open: true,
    },
});