import { useRef } from "react";

/** Which keys arrived after the list was first shown: those items slide in (class "enter");
 * the ones already there on first load do not move. */
export function useArrivals(keys: (string | number)[] | undefined): Set<string | number> {
  const seen = useRef<Set<string | number> | null>(null);
  const fresh = useRef(new Set<string | number>());
  if (keys) {
    if (seen.current === null) seen.current = new Set(keys);
    for (const k of keys) {
      if (!seen.current.has(k)) {
        seen.current.add(k);
        fresh.current.add(k);
      }
    }
  }
  return fresh.current;
}
