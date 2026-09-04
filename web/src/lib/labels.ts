// Translates internal pipeline slugs (match/*.py's `reason` values) into
// sentences a merchant with no finance background can read without
// looking anything up. New reason slugs fall back to a readable version
// of the slug itself rather than throwing.
const REASON_LABELS: Record<string, string> = {
  amount_mismatch: "The amount doesn't match what we expected",
  missing_settlement: "We can't find a matching settlement for this",
  no_fuzzy_match: "Nothing close enough to match this to",
  t2_internal_mismatch: "The numbers don't add up internally",
  no_member_payments: "No payments found for this settlement",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

const TIER_LABELS: Record<string, string> = {
  T1: "Matched automatically",
  T2: "Matched after an internal check",
  T3: "Matched by closest fit",
};

export function tierLabel(tier: string): string {
  return TIER_LABELS[tier] ?? tier;
}
