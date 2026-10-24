import { useState } from "react";
import { useI18n } from "../i18n/context";
import type { Lang } from "../i18n/strings";
import { LanguagePicker } from "./LanguagePicker";

interface Props {
  language: Lang | null;
  onLanguageChange: (lang: Lang) => void;
  onAccept: () => Promise<void>;
}

/** The consent screen shown on first use (M6-T03, TRD §11): the user picks a language, reads
 * the privacy notice, and must agree before any chatting can save a profile. */
export function ConsentScreen({ language, onLanguageChange, onAccept }: Props) {
  const { t } = useI18n();
  const [accepting, setAccepting] = useState(false);

  async function handleAccept() {
    setAccepting(true);
    try {
      await onAccept();
    } finally {
      setAccepting(false);
    }
  }

  return (
    <main className="flex min-h-full flex-col items-center justify-center gap-6 bg-teal-50 px-5 py-10">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-teal-900">{t("appName")}</h1>
        <p className="mt-1 text-base text-teal-700">{t("tagline")}</p>

        <div className="mt-6">
          <p className="mb-2 text-sm font-medium text-gray-700">{t("languagePickerLabel")}</p>
          <LanguagePicker
            value={language}
            onChange={onLanguageChange}
            label={t("languagePickerLabel")}
          />
        </div>

        <h2 className="mt-6 text-lg font-semibold text-gray-900">{t("consentTitle")}</h2>
        <p className="mt-2 text-base leading-relaxed text-gray-700">{t("consentBody")}</p>
        <p className="mt-3 text-sm leading-relaxed text-gray-500">{t("consentSensitiveNote")}</p>

        <button
          type="button"
          onClick={handleAccept}
          disabled={accepting || !language}
          className="mt-6 min-h-12 w-full rounded-xl bg-teal-700 px-4 text-lg font-semibold text-white transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("consentAccept")}
        </button>
      </div>
    </main>
  );
}
