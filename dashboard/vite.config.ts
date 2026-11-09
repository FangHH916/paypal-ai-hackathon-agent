// Builds one ES module, web/dashboard/star-dashboard.js, that Star's own server serves from its own origin. It runs only
// in the Dashboard's frame (web/dashboard.html, /dashboard-frame), so nothing on the other pages loads it; scripts still
// come only from this origin (see DASHBOARD_CSP in app/security.py).
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The AG packages ship their own minified ES builds; use those instead of re-minifying the readable ones.
const MIN = ['ag-studio', 'ag-grid-enterprise', 'ag-grid-community', 'ag-charts-enterprise', 'ag-charts-community', 'ag-charts-core', 'ag-stack'];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: MIN.map((name) => ({
      find: new RegExp(`^${name}$`),
      replacement: fileURLToPath(new URL(`./node_modules/${name}/dist/package/main.esm.min.mjs`, import.meta.url)),
    })),
  },
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../web/dashboard',
    emptyOutDir: true,
    target: 'es2022',
    minify: 'esbuild',
    cssCodeSplit: false,
    lib: { entry: 'src/main.tsx', formats: ['es'], fileName: () => 'star-dashboard.js' },
    rollupOptions: { output: { inlineDynamicImports: true } },
    chunkSizeWarningLimit: 8000,
  },
});
