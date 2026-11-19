import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// Port 8610 is registered for llm-steward-admin in the KB ports registry.
// Routes are code-split for the bundle only: under vitest a lazy route chunk is transformed on first render,
// which under parallel workers took over the 1 s findBy* budget of each file's first test.
export default defineConfig(({ mode }) => ({
  plugins: [tanstackRouter({ target: 'react', autoCodeSplitting: mode !== 'test' }), react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 8610, strictPort: true, host: '127.0.0.1' },
  preview: { port: 8610, strictPort: true, host: '127.0.0.1' },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    globals: true,
  },
}))
