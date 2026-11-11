// The widget's client, generated from the backend's OpenAPI spec (the only contract). Components never
// use the generated module-level client: each call passes its own (base URL and the user token), so
// the widget cannot clash with a host app that uses the same generator.
import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: '../../../sdk/python/openapi.json',
  output: { path: 'src/client' },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
})
