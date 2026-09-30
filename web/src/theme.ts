/** Step 5b stage 0: three design directions, picked with ?theme= (remembered for the tab). */
// industrial is the lead direction (the default); the others stay for comparison
export const THEMES = ["industrial", "aurora", "daylight"] as const;
export type Theme = (typeof THEMES)[number];
export const DEFAULT_THEME: Theme = "industrial";

const isTheme = (t: string | null | undefined): t is Theme =>
  !!t && (THEMES as readonly string[]).includes(t);

/** The theme from the URL, else the one this tab last used, else the default. */
export function readTheme(search: string = window.location.search): Theme {
  const fromUrl = new URLSearchParams(search).get("theme");
  if (isTheme(fromUrl)) return fromUrl;
  try {
    const kept = sessionStorage.getItem("theme");
    if (isTheme(kept)) return kept;
  } catch { /* storage may be unavailable */ }
  return DEFAULT_THEME;
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try { sessionStorage.setItem("theme", theme); } catch { /* ignore */ }
}
