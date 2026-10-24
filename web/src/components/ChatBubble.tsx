import { useI18n } from "../i18n/context";
import type { ChatMessage } from "../types/chat";
import { EligibilitySection } from "./EligibilitySection";
import { ReplyAudioButton } from "./ReplyAudioButton";

interface Props {
  message: ChatMessage;
}

export function ChatBubble({ message }: Props) {
  const { t } = useI18n();
  const isUser = message.role === "user";

  return (
    <div
      className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
      role="group"
      aria-label={isUser ? t("you") : t("sahayak")}
    >
      <div className={`flex max-w-[85%] flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
        <div
          className={`rounded-2xl px-4 py-3 text-base leading-relaxed whitespace-pre-wrap ${
            isUser
              ? "bg-teal-700 text-white"
              : message.kind === "error"
                ? "bg-red-50 text-red-800"
                : "bg-white text-gray-900 shadow-sm"
          } ${message.pending ? "opacity-60" : ""}`}
        >
          {message.text}
        </div>

        {!isUser && message.audioUrl && <ReplyAudioButton audioUrl={message.audioUrl} />}

        {!isUser && message.eligibility && message.citations && (
          <div className="w-full">
            <EligibilitySection eligibility={message.eligibility} citations={message.citations} />
          </div>
        )}
      </div>
    </div>
  );
}
