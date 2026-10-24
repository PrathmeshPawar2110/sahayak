import { createContext, useContext } from "react";
import type { Lang, StringKey } from "./strings";

export interface I18nValue {
  lang: Lang;
  t: (key: StringKey) => string;
}

export const I18nContext = createContext<I18nValue | null>(null);

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside an I18nProvider");
  return value;
}
