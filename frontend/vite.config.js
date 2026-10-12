import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

/* Im Betrieb liefert das Backend Oberflaeche und API unter einem Port aus.
   Beim Entwickeln laeuft Vite getrennt davon; der Proxy reicht /api so, wie es
   ist, an das Backend weiter (server.py nimmt das Praefix dort selbst ab). Ein
   Umschreiben hier wuerde den Unterschied zwischen Entwicklung und Betrieb nur
   verstecken. Standard ist das lokal gestartete Backend auf 8095. */
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8095';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
});
