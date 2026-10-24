import { useI18n } from "../i18n/context";
import { useSchemeDetails } from "../hooks/useSchemeDetails";
import { EligibilityCard } from "./EligibilityCard";
import type { CitationResponse, EligibilityGroups } from "../api/types";

interface Props {
  eligibility: EligibilityGroups;
  citations: CitationResponse[];
}

export function EligibilitySection({ eligibility, citations }: Props) {
  const { t, lang } = useI18n();
  const allSchemes = [
    ...eligibility.ready,
    ...eligibility.action_needed,
    ...eligibility.provisional,
  ];
  const details = useSchemeDetails(allSchemes.map((s) => s.scheme_id), lang);
  if (allSchemes.length === 0) return null;

  return (
    <div className="mt-2 flex flex-col gap-3">
      {eligibility.ready.map((scheme) => (
        <EligibilityCard
          key={scheme.scheme_id}
          scheme={scheme}
          detail={details[scheme.scheme_id]}
          citations={citations}
          badge={t("eligibilityReady")}
          badgeClassName="bg-emerald-100 text-emerald-800"
        />
      ))}
      {eligibility.action_needed.map((scheme) => (
        <EligibilityCard
          key={scheme.scheme_id}
          scheme={scheme}
          detail={details[scheme.scheme_id]}
          citations={citations}
          badge={t("eligibilityActionNeeded")}
          badgeClassName="bg-amber-100 text-amber-800"
        />
      ))}
      {eligibility.provisional.map((scheme) => (
        <EligibilityCard
          key={scheme.scheme_id}
          scheme={scheme}
          detail={details[scheme.scheme_id]}
          citations={citations}
          badge={t("eligibilityProvisional")}
          badgeClassName="bg-sky-100 text-sky-800"
        />
      ))}
    </div>
  );
}
