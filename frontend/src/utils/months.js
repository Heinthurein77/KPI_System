// One shared copy of the English month names (review finding m17).
// Six files used to each define their own MONTHS / MONTH_NAMES_EN /
// DEFAULT_MONTHS list; they all import from here now so the names can
// never drift apart.
export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// Alias kept for the files that historically called it MONTH_NAMES_EN.
export const MONTH_NAMES_EN = MONTHS;
