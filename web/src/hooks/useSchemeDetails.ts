import { useEffect, useRef, useState } from "react";
import { getScheme } from "../api/client";
import type { SchemeSummary } from "../api/types";
import type { Lang } from "../i18n/strings";

/** Looks up each scheme's benefits text for the eligibility cards (M6-T06); `SchemeResult`
 * (from a chat turn) doesn't carry it, so this fills in from `GET /v1/schemes/{id}`, once per
 * scheme id, cached for the component's lifetime. */
export function useSchemeDetails(schemeIds: string[], language: Lang) {
  const [details, setDetails] = useState<Record<string, SchemeSummary>>({});
  const fetched = useRef(new Set<string>());

  useEffect(() => {
    const missing = schemeIds.filter((id) => !fetched.current.has(id));
    if (missing.length === 0) return;
    for (const id of missing) fetched.current.add(id);
    for (const id of missing) {
      getScheme(id, language)
        .then((scheme) => setDetails((prev) => ({ ...prev, [id]: scheme })))
        .catch(() => {
          // A missing/archived scheme just shows the card without benefits text.
        });
    }
  }, [schemeIds, language]);

  return details;
}
