import { useMemo, type ReactNode } from "react";
import { I18nContext, type I18nValue } from "./context";
import { type Lang, strings } from "./strings";

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo<I18nValue>(() => ({ lang, t: (key) => strings[lang][key] }), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
