/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL for TEC map blobs (R2). Falls back to the prod custom domain. */
  readonly VITE_TEC_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
