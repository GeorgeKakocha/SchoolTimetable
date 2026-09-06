/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SCHOOL_ID: string | undefined;
  readonly VITE_ACADEMIC_YEAR_ID: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
