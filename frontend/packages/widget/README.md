# @llm-steward/widget

React components for the end users of an app that uses llm-steward:

- **`<SuggestionCard />`** — the confirmation card. "Save this?" with what the assistant drafted from the
  conversation. The user saves it for the team (a reviewer checks it first) or just for themselves,
  edits it (then it is kept just for them), or says no, optionally "stop suggesting things to me"
  (kept as a memory of theirs: forgetting it in the MemoryPanel undoes it).
- **`<MemoryPanel />`** — what the assistant remembers about the user, with "Forget" (asks first).

Whatever the user answers, nothing becomes verified knowledge: only a reviewer verifies.

## Auth: a user token, never an API key

Your backend mints a short-lived token for the signed-in user and hands it to the page. An API key in
the browser would let anyone with the page act for every user.

```
POST /v1/tokens/user   Authorization: Bearer stw_…   {"user_id": "u_123", "ttl_seconds": 900}
→ {"token": "…", "expires_at": "…"}
```

## Use

```tsx
import { MemoryPanel, SuggestionCard } from '@llm-steward/widget'
import '@llm-steward/widget/styles.css' // optional plain defaults (`lsw-` classes, nothing global)

const connection = { baseUrl: 'https://steward.example.com', token: userToken }

// `suggestions` come back with POST /v1/retrieve and /v1/episodes for this user and session
{suggestions.map((s) => (
  <SuggestionCard key={s.id} connection={connection} suggestion={s} onDecided={refresh} />
))}

<MemoryPanel connection={connection} />
```

React 18 or later is a peer dependency. ESM only; about 7 kB gzipped. Without the stylesheet the
components are unstyled: style the `lsw-*` classes, or pass `className`.

The API client is generated from llm-steward's OpenAPI spec (`pnpm generate-client`). Each component
uses its own client instance, so it cannot clash with a host app that uses the same generator.
