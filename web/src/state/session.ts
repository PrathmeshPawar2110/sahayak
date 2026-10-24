import { useCallback, useEffect, useState } from "react";
import { createSession, deleteSession as apiDeleteSession, setConsent } from "../api/client";
import type { SessionResponse } from "../api/types";
import type { Lang } from "../i18n/strings";

const STORAGE_KEY = "sahayak.session";
export const POLICY_VERSION = "2026-10-24";

interface StoredSession {
  sessionId: string;
  language: Lang | null;
  hasConsented: boolean;
}

function load(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

function save(session: StoredSession | null): void {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private browsing or storage disabled: the session just won't survive a reload.
  }
}

export interface SessionController {
  loading: boolean;
  sessionId: string | null;
  language: Lang | null;
  hasConsented: boolean;
  setLanguage: (lang: Lang) => Promise<void>;
  consent: () => Promise<void>;
  deleteEverything: () => Promise<void>;
}

/** The anonymous session (TRD §11; accounts arrive in M10): created on first load, kept in
 * `localStorage` so a reload doesn't lose the conversation, and recreated if the server has
 * forgotten it (expiry or a prior delete). */
export function useSession(): SessionController {
  const [loading, setLoading] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [language, setLanguageState] = useState<Lang | null>(null);
  const [hasConsented, setHasConsented] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function init() {
      const stored = load();
      try {
        const response: SessionResponse = await createSession(
          "web",
          stored?.language ?? undefined,
        );
        if (cancelled) return;
        const consented = Boolean(stored?.hasConsented && stored.sessionId === response.session_id);
        setSessionId(response.session_id);
        setLanguageState(response.language ?? stored?.language ?? null);
        setHasConsented(consented || "profile" in response.consents);
        save({
          sessionId: response.session_id,
          language: response.language ?? stored?.language ?? null,
          hasConsented: consented || "profile" in response.consents,
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void init();
    return () => {
      cancelled = true;
    };
  }, []);

  const setLanguage = useCallback(
    async (lang: Lang) => {
      setLanguageState(lang);
      if (sessionId) save({ sessionId, language: lang, hasConsented });
    },
    [sessionId, hasConsented],
  );

  const consent = useCallback(async () => {
    if (!sessionId) return;
    await setConsent(sessionId, ["profile"], true, POLICY_VERSION);
    setHasConsented(true);
    save({ sessionId, language, hasConsented: true });
  }, [sessionId, language]);

  const deleteEverything = useCallback(async () => {
    if (!sessionId) return;
    await apiDeleteSession(sessionId);
    save(null);
    setSessionId(null);
    setHasConsented(false);
    setLoading(true);
    const response = await createSession("web", language ?? undefined);
    setSessionId(response.session_id);
    save({ sessionId: response.session_id, language, hasConsented: false });
    setLoading(false);
  }, [sessionId, language]);

  return { loading, sessionId, language, hasConsented, setLanguage, consent, deleteEverything };
}
