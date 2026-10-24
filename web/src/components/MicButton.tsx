import { useI18n } from "../i18n/context";
import type { VoiceState } from "../hooks/useVoiceStream";

interface Props {
  state: VoiceState;
  onStart: () => void;
  onStop: () => void;
}

/** The big mic button (M6-T05): tap to start listening, tap again to stop. Large enough for a
 * thumb (min 56px) and announces its state for screen readers. */
export function MicButton({ state, onStart, onStop }: Props) {
  const { t } = useI18n();
  const isListening = state === "listening";
  const isBusy = state === "connecting" || state === "thinking" || state === "speaking";

  return (
    <button
      type="button"
      onClick={isListening ? onStop : onStart}
      disabled={isBusy}
      aria-label={isListening ? t("micStop") : t("micStart")}
      aria-pressed={isListening}
      className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-white shadow-md transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
        isListening ? "bg-red-600 animate-pulse" : "bg-teal-700 hover:bg-teal-800"
      }`}
    >
      {isListening ? (
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-6 w-6" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-7 w-7" aria-hidden="true">
          <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3Z" />
          <path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.08A7 7 0 0 0 19 11Z" />
        </svg>
      )}
    </button>
  );
}
