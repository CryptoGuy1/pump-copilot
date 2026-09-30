import { createContext, useContext } from "react";

/** Step 5b: three design directions, picked with ?theme= and remembered in this browser.
 * industrial is the lead direction and the default. */
export const THEMES = ["industrial", "aurora", "daylight"] as const;
export type Theme = (typeof THEMES)[number];
export const DEFAULT_THEME: Theme = "industrial";
const KEY = "pumpcopilot.theme";

const isTheme = (t: string | null | undefined): t is Theme =>
  !!t && (THEMES as readonly string[]).includes(t);

/** The theme from the URL, else the one chosen last in this browser, else the default. */
export function readTheme(search: string = window.location.search): Theme {
  const fromUrl = new URLSearchParams(search).get("theme");
  if (isTheme(fromUrl)) return fromUrl;
  try {
    const kept = localStorage.getItem(KEY);
    if (isTheme(kept)) return kept;
  } catch { /* storage may be unavailable */ }
  return DEFAULT_THEME;
}

/** Apply a theme and remember the choice. */
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* ignore */ }
}

export const ThemeContext = createContext<Theme>(DEFAULT_THEME);
export const useTheme = () => useContext(ThemeContext);
