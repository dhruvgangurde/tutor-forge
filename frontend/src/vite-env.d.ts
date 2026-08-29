/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute backend origin used in production builds (F2). Unset in dev. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
