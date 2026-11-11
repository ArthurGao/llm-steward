// @llm-steward/widget: embeddable React components for end users (design §9). Give them a
// Connection with a user token your backend minted (`POST /v1/tokens/user`), never an API key.
// Unstyled by default; `import '@llm-steward/widget/styles.css'` for plain defaults (`lsw-` classes).
export { SuggestionCard, type SuggestionCardProps } from './SuggestionCard'
export { MemoryPanel, type MemoryPanelProps } from './MemoryPanel'
export {
  StewardError,
  stewardApi,
  type Connection,
  type Decision,
  type DecisionResult,
  type Memory,
  type Suggestion,
} from './api'
