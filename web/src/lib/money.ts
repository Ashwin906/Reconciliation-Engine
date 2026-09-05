// Display-only paise -> rupee formatting. Mirrors core/money.py's
// format_paise -- never feed this back into a calculation, same rule as
// the Python side.
export function formatPaise(paise: number): string {
  const rupees = paise / 100;
  return rupees.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
