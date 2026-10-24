import { LANGUAGES, type Lang } from "../i18n/strings";

interface Props {
  value: Lang | null;
  onChange: (lang: Lang) => void;
  label: string;
}

export function LanguagePicker({ value, onChange, label }: Props) {
  return (
    <div role="radiogroup" aria-label={label} className="flex gap-2">
      {LANGUAGES.map(({ code, label: langLabel }) => (
        <button
          key={code}
          type="button"
          role="radio"
          aria-checked={value === code}
          onClick={() => onChange(code)}
          className={`min-h-11 min-w-20 rounded-full px-4 py-2 text-base font-medium transition-colors ${
            value === code
              ? "bg-teal-700 text-white"
              : "bg-white text-teal-900 ring-1 ring-inset ring-teal-300 hover:bg-teal-50"
          }`}
        >
          {langLabel}
        </button>
      ))}
    </div>
  );
}
