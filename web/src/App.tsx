import { useEffect } from "react";
import { ChatScreen } from "./components/ChatScreen";
import { ConsentScreen } from "./components/ConsentScreen";
import { I18nProvider } from "./i18n";
import { useSession } from "./state/session";

export default function App() {
  const session = useSession();
  const lang = session.language ?? "en";

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  if (session.loading || !session.sessionId) {
    return (
      <div className="flex h-full items-center justify-center bg-teal-50">
        <p className="text-teal-700">…</p>
      </div>
    );
  }

  return (
    <I18nProvider lang={lang}>
      {session.hasConsented ? (
        <ChatScreen sessionId={session.sessionId} onDeleteEverything={session.deleteEverything} />
      ) : (
        <ConsentScreen
          language={session.language}
          onLanguageChange={session.setLanguage}
          onAccept={session.consent}
        />
      )}
    </I18nProvider>
  );
}
