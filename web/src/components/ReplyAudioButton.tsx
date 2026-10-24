import { useRef, useState } from "react";
import { absoluteUrl } from "../api/client";
import { useI18n } from "../i18n/context";

interface Props {
  audioUrl: string;
}

/** Plays a reply's spoken audio (M6-T04): a small speaker button beside the chat bubble. */
export function ReplyAudioButton({ audioUrl }: Props) {
  const { t } = useI18n();
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  function toggle() {
    if (!audioRef.current) {
      audioRef.current = new Audio(absoluteUrl(audioUrl));
      audioRef.current.onended = () => setPlaying(false);
      audioRef.current.onpause = () => setPlaying(false);
    }
    if (playing) {
      audioRef.current.pause();
    } else {
      void audioRef.current.play();
      setPlaying(true);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={t("playReply")}
      aria-pressed={playing}
      className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-teal-100 text-teal-800 hover:bg-teal-200"
    >
      {playing ? (
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden="true">
          <rect x="6" y="5" width="4" height="14" />
          <rect x="14" y="5" width="4" height="14" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden="true">
          <path d="M7 5v14l12-7z" />
        </svg>
      )}
    </button>
  );
}
