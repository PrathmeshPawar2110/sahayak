import type {
  Channel,
  ConsentResponse,
  ErrorEnvelope,
  MessageResponse,
  ProfileResponse,
  SchemeSummary,
  SessionResponse,
} from "./types";
import type { Lang } from "../i18n/strings";

export const API_BASE = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export const WS_BASE = API_BASE.replace(/^http/, "ws");

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    throw new ApiError(
      response.status,
      body?.error.code ?? "unknown_error",
      body?.error.message ?? `Request failed (${response.status})`,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createSession(channel: Channel = "web", language?: Lang): Promise<SessionResponse> {
  return request("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ channel, language: language ?? null }),
  });
}

export function setConsent(
  sessionId: string,
  purposes: ("profile" | "sensitive")[],
  granted: boolean,
  policyVersion: string,
): Promise<ConsentResponse> {
  return request(`/v1/sessions/${sessionId}/consent`, {
    method: "POST",
    body: JSON.stringify({ purposes, granted, policy_version: policyVersion }),
  });
}

export function postMessage(sessionId: string, text: string): Promise<MessageResponse> {
  return request(`/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function getProfile(sessionId: string): Promise<ProfileResponse> {
  return request(`/v1/sessions/${sessionId}/profile`);
}

export function deleteSession(sessionId: string): Promise<void> {
  return request(`/v1/sessions/${sessionId}`, { method: "DELETE" });
}

export function getScheme(schemeId: string, language: Lang): Promise<SchemeSummary> {
  return request(`/v1/schemes/${schemeId}?language=${language}`);
}

/** `reply_audio_url` and similar fields come back as paths relative to the API. */
export function absoluteUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export function voiceStreamUrl(sessionId: string): string {
  return `${WS_BASE}/v1/sessions/${sessionId}/stream`;
}
