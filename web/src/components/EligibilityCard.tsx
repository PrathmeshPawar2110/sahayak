import { useI18n } from "../i18n/context";
import type { CitationResponse, SchemeResult, SchemeSummary } from "../api/types";

interface Props {
  scheme: SchemeResult;
  detail?: SchemeSummary;
  citations: CitationResponse[];
  badge: string;
  badgeClassName: string;
}

function formatDate(value: string | null): string | null {
  if (!value) return null;
  try {
    return new Date(value).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return value;
  }
}

/** One recommended scheme (M6-T06): name, benefit, why it matched, its citations and when the
 * content was last verified. */
export function EligibilityCard({ scheme, detail, citations, badge, badgeClassName }: Props) {
  const { t } = useI18n();
  const lastVerified = formatDate(scheme.last_verified_at);
  const schemeCitations = citations.filter((c) => c.scheme_id === scheme.scheme_id);

  return (
    <article className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-lg font-semibold text-gray-900">{scheme.name}</h3>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${badgeClassName}`}>
          {badge}
        </span>
      </div>

      {detail?.benefits && <p className="mt-2 text-base text-gray-700">{detail.benefits}</p>}

      {scheme.reasons.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-sm text-gray-600">
          {scheme.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {scheme.provisional && (
        <p className="mt-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-800">
          {t("provisionalCaveat")}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        {scheme.official_url && (
          <a
            href={scheme.official_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-medium text-teal-700 underline underline-offset-2 hover:text-teal-900"
          >
            {t("officialLink")}
          </a>
        )}
        {lastVerified && (
          <span className="text-gray-500">
            {t("lastVerified")}: {lastVerified}
          </span>
        )}
      </div>

      {schemeCitations.length > 0 && (
        <details className="mt-2 text-sm text-gray-500">
          <summary className="cursor-pointer select-none font-medium">{t("sources")}</summary>
          <ul className="mt-1 space-y-1 pl-1">
            {schemeCitations.map((citation) => (
              <li key={citation.chunk_id}>
                [{citation.number}]{" "}
                <a
                  href={citation.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline underline-offset-2 hover:text-teal-700"
                >
                  {citation.section}
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
