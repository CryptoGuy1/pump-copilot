import { useEffect } from "react";

/** The browser tab's title for this page: "<page> · pump-copilot". */
export function usePageTitle(page: string | null | undefined) {
  useEffect(() => {
    document.title = page ? `${page} · pump-copilot` : "pump-copilot";
  }, [page]);
}
