import { useRef, useState } from "react";
import { ApiError, postMessage, voiceStreamUrl } from "../api/client";
import { useI18n } from "../i18n/context";
import { useVoiceStream } from "../hooks/useVoiceStream";
import type { ChatMessage } from "../types/chat";
import { ChatBubble } from "./ChatBubble";
import { MicButton } from "./MicButton";
import { ProfilePanel } from "./ProfilePanel";
import { ConfirmDialog } from "./ConfirmDialog";

interface Props {
  sessionId: string;
  onDeleteEverything: () => Promise<void>;
}

function assistantMessage(id: string, text: string, kind?: ChatMessage["kind"]): ChatMessage {
  return { id, role: "assistant", text, kind };
}

export function ChatScreen({ sessionId, onDeleteEverything }: Props) {
  const { t } = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [partialTranscript, setPartialTranscript] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [profileRefreshKey, setProfileRefreshKey] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  function scrollToEnd() {
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
    });
  }

  async function sendText(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setInput("");
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text: trimmed }]);
    setSending(true);
    scrollToEnd();
    try {
      const reply = await postMessage(sessionId, trimmed);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: reply.reply_text,
          kind: reply.kind,
          audioUrl: reply.reply_audio_url,
          eligibility: reply.eligibility,
          citations: reply.citations,
        },
      ]);
      setProfileRefreshKey((key) => key + 1);
      if (reply.session_deleted) await onDeleteEverything();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : t("errorGeneric");
      setMessages((prev) => [...prev, assistantMessage(crypto.randomUUID(), message, "error")]);
    } finally {
      setSending(false);
      scrollToEnd();
    }
  }

  const voice = useVoiceStream({
    wsUrl: voiceStreamUrl(sessionId),
    onPartialTranscript: setPartialTranscript,
    onFinalTranscript: (transcript) => {
      setPartialTranscript("");
      const text = transcript.text;
      if (text) {
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text }]);
        scrollToEnd();
      }
    },
    onReply: (reply) => {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: reply.reply_text,
          kind: reply.kind,
          eligibility: reply.eligibility,
          citations: reply.citations,
        },
      ]);
      setProfileRefreshKey((key) => key + 1);
      scrollToEnd();
    },
    onError: (code) => {
      const message = code === "mic_permission" ? t("errorMicPermission") : t("errorGeneric");
      setMessages((prev) => [...prev, assistantMessage(crypto.randomUUID(), message, "error")]);
    },
  });

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <header className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-3">
        <h1 className="text-lg font-bold text-teal-900">{t("appName")}</h1>
        <button
          type="button"
          onClick={() => setProfileOpen(true)}
          className="min-h-10 rounded-full bg-teal-50 px-3 text-sm font-medium text-teal-800 hover:bg-teal-100"
        >
          {t("profileButton")}
        </button>
      </header>

      <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-4" aria-live="polite">
        <div className="flex flex-col gap-4">
          {messages.map((message) => (
            <ChatBubble key={message.id} message={message} />
          ))}
          {voice.state === "listening" && partialTranscript && (
            <ChatBubble
              message={{ id: "partial", role: "user", text: partialTranscript, pending: true }}
            />
          )}
          {(sending || voice.state === "thinking") && (
            <p className="text-sm text-gray-500">{t("thinking")}</p>
          )}
          {voice.state === "listening" && (
            <p className="text-sm text-gray-500">{t("listening")}</p>
          )}
        </div>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void sendText(input);
        }}
        className="flex items-end gap-2 border-t border-gray-200 bg-white p-3"
      >
        <MicButton state={voice.state} onStart={voice.start} onStop={voice.stop} />
        <label className="sr-only" htmlFor="chat-input">
          {t("chatPlaceholder")}
        </label>
        <textarea
          id="chat-input"
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void sendText(input);
            }
          }}
          placeholder={t("chatPlaceholder")}
          className="min-h-11 flex-1 resize-none rounded-xl border border-gray-300 px-4 py-2.5 text-base focus:border-teal-600"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="min-h-11 shrink-0 rounded-xl bg-teal-700 px-4 text-base font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("send")}
        </button>
      </form>

      <ProfilePanel
        sessionId={sessionId}
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        onRequestDelete={() => setConfirmDeleteOpen(true)}
        refreshKey={profileRefreshKey}
      />
      <ConfirmDialog
        open={confirmDeleteOpen}
        title={t("profileDeleteConfirmTitle")}
        body={t("profileDeleteConfirmBody")}
        confirmLabel={t("profileDelete")}
        onCancel={() => setConfirmDeleteOpen(false)}
        onConfirm={() => {
          setConfirmDeleteOpen(false);
          setProfileOpen(false);
          setMessages([]);
          void onDeleteEverything();
        }}
      />
    </div>
  );
}
