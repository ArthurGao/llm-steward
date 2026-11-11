// The Admin's client, generated from the backend's OpenAPI spec (the only contract, frontend/AGENTS.md).
// The spec is the one committed for the SDK (sdk/python/openapi.json), which a backend test keeps in
// step with the API; regenerate with `pnpm generate-client` after an API change.
import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: '../../../sdk/python/openapi.json',
  output: { path: 'src/client' },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
})
