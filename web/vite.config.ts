import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  optimizeDeps: {
    // Pre-declared explicitly rather than left to Vite's lazy crawler.
    // Without this, react-dom/lucide-react/@base-ui's submodules were
    // discovered mid-render on first paint, triggering a SECOND
    // dependency pre-bundle pass while the page was already loaded --
    // the classic Vite "Invalid hook call / two copies of react-dom"
    // symptom, but caused by two different esbuild-optimized bundle
    // hashes loaded in the same session, not an actual duplicate
    // react-dom package on disk (confirmed: only one copy exists in
    // node_modules). React 19 recovered from the failed first pass and
    // the page still rendered correctly, but the console errors were
    // real and would confuse anyone opening devtools. Listing the
    // dependencies up front means Vite's initial optimize pass already
    // includes them -- no lazy rescan, no mid-session module swap.
    include: ['react', 'react-dom', 'lucide-react', '@base-ui/react/tabs', '@base-ui/react/dialog', '@base-ui/react/select'],
  },
})
