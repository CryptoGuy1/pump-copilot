/** The app's routes: the router (App.tsx) uses these paths, and the static snapshot build
 * writes a page for each (scripts/static-routes.ts), so the two cannot disagree. */
export const ROUTES = {
  fleet: "/",
  assetDay: "/assets/:asset/:day",
  cases: "/cases",
  case: "/cases/:id",
  replay: "/replay",
  evaluation: "/evaluation",
  dataQuality: "/data-quality",
  assumptions: "/assumptions",
  design: "/design",
  about: "/about",
} as const;

/** For a route with parameters: the snapshot file that must exist for a value to be shown, so
 * the build writes a page for each exported case and asset-day only. */
export const ROUTE_DATA: Partial<Record<keyof typeof ROUTES, string>> = {
  assetDay: "api/assets/:asset/days/:day.json",
  case: "api/cases/:id.json",
};
