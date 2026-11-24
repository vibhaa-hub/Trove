/** Display helpers. All formatting uses the browser locale via Intl. */

const SPECIAL: Record<string, string> = {
  nda: "NDA",
  otp: "OTP",
  mfa: "MFA",
  sms: "SMS",
  id: "ID",
  imap: "IMAP",
  ppf: "PPF",
  epf: "EPF",
  nps: "NPS",
};

/** "bank_account" -> "Bank account" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "";
  const words = value.replace(/[_-]+/g, " ").trim().split(/\s+/);
  return words
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (SPECIAL[lower]) return SPECIAL[lower];
      return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
    })
    .join(" ");
}

function toDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) return value ?? "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) return value ?? "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  const date = toDate(value);
  if (!date) return "never";
  const seconds = Math.round((date.getTime() - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function percent(value: number): string {
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 0 }).format(value);
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export const DOCUMENT_CATEGORIES: Record<string, string> = {
  death_certificate: "Death certificate",
  identity_document: "Identity document",
  will: "Will",
  trust_deed: "Trust deed",
  property_document: "Property document",
  insurance_policy: "Insurance policy",
  bank_statement: "Bank statement",
  investment_statement: "Investment statement",
  pension_record: "Pension record",
  tax_return: "Tax return",
  medical_record: "Medical record",
  other: "Other",
};

export const WORKFLOW_STAGES = [
  "intake",
  "death_verification",
  "evidence_review",
  "asset_verification",
  "beneficiary_review",
  "claims",
  "settlement",
] as const;

export const CLAIM_STATUSES = [
  "not_started",
  "documents_requested",
  "submitted",
  "under_review",
  "settled",
  "rejected",
] as const;

/** 12+ characters and at least 3 of: lower, upper, digit, symbol (mirrors the server rule). */
export function passwordChecks(password: string) {
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length;
  return { length: password.length >= 12, classes: classes >= 3, classCount: classes };
}
