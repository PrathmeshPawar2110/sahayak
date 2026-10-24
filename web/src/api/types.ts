// Hand-trimmed to the fields the UI uses; the generated `schema.ts` (openapi-typescript, see
// `npm run generate:api`) is the full source of truth and `client.ts` is checked against it.
import type { Lang } from "../i18n/strings";

export type Channel = "web" | "whatsapp";
export type ReplyKind = "answer" | "question" | "notice" | "error";
export type Intent =
  | "ask_eligibility"
  | "provide_info"
  | "ask_scheme_info"
  | "manage_reminders"
  | "reset"
  | "delete_data"
  | "other";

export interface ConsentRecord {
  granted_at: string;
  policy_version: string;
}

export interface SessionResponse {
  session_id: string;
  channel: Channel;
  language: Lang | null;
  expires_at: string;
  consents: Record<string, ConsentRecord>;
}

export interface ConsentResponse {
  consents: Record<string, ConsentRecord>;
}

export interface SchemeResult {
  scheme_id: string;
  name: string;
  provisional: boolean;
  reasons: string[];
  official_url: string | null;
  last_verified_at: string | null;
}

export interface EligibilityGroups {
  ready: SchemeResult[];
  action_needed: SchemeResult[];
  provisional: SchemeResult[];
}

export interface CitationResponse {
  number: number;
  chunk_id: string;
  scheme_id: string;
  scheme_name: string;
  section: string;
  source_url: string;
  last_verified_at: string | null;
}

export interface MessageResponse {
  reply_text: string;
  reply_audio_url: string | null;
  kind: ReplyKind;
  language: Lang;
  intent: Intent | null;
  question_field: string | null;
  eligibility: EligibilityGroups;
  citations: CitationResponse[];
  session_deleted: boolean;
}

export interface FieldSourceResponse {
  source: string;
  captured_at: string;
  confidence: number;
  valid_until: string | null;
}

export interface ProfileResponse {
  session_id: string;
  language: Lang | null;
  profile: Record<string, unknown>;
  field_sources: Record<string, FieldSourceResponse>;
}

export interface ErrorEnvelope {
  error: { code: string; message: string; details?: unknown };
}

export type SchemeLevel = "central" | "state";
export type SchemeStatus = "active" | "provisional" | "discontinued" | "archived";

export interface SchemeSummary {
  id: string;
  name: string;
  names: Record<Lang, string>;
  level: SchemeLevel;
  state: string | null;
  status: SchemeStatus;
  benefits: string | null;
  official_url: string | null;
  last_verified_at: string | null;
}
