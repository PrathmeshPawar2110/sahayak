import { useEffect, useState } from "react";
import { getProfile } from "../api/client";
import type { ProfileResponse } from "../api/types";
import { useI18n } from "../i18n/context";

interface Props {
  sessionId: string;
  open: boolean;
  onClose: () => void;
  onRequestDelete: () => void;
  refreshKey: number;
}

function label(field: string): string {
  return field
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

/** "What Sahayak knows about me" (M6-T07): every saved profile field, with a hint for how to
 * correct one (there's no edit endpoint yet; M10 adds accounts), and a delete-everything button. */
export function ProfilePanel({ sessionId, open, onClose, onRequestDelete, refreshKey }: Props) {
  const { t } = useI18n();
  const [profile, setProfile] = useState<ProfileResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getProfile(sessionId).then((response) => {
      if (!cancelled) setProfile(response);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, open, refreshKey]);

  if (!open) return null;

  const fields = Object.entries(profile?.profile ?? {});

  return (
    <div
      className="fixed inset-0 z-20 flex items-end justify-center bg-black/40 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={t("profileTitle")}
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-t-2xl bg-white p-5 shadow-lg sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">{t("profileTitle")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="flex h-9 w-9 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100"
          >
            ✕
          </button>
        </div>

        {fields.length === 0 ? (
          <p className="mt-4 text-base text-gray-600">{t("profileEmpty")}</p>
        ) : (
          <dl className="mt-4 divide-y divide-gray-100">
            {fields.map(([field, value]) => (
              <div key={field} className="flex justify-between gap-4 py-2">
                <dt className="text-sm text-gray-500">{label(field)}</dt>
                <dd className="text-right text-sm font-medium text-gray-900">
                  {formatValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <p className="mt-4 text-sm text-gray-500">{t("profileEditHint")}</p>

        <button
          type="button"
          onClick={onRequestDelete}
          className="mt-6 min-h-11 w-full rounded-xl border border-red-200 px-4 text-base font-semibold text-red-700 hover:bg-red-50"
        >
          {t("profileDelete")}
        </button>
      </div>
    </div>
  );
}
