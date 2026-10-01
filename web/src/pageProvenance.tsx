import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ProvenanceStrip, type ProvenanceData } from "./components/ui";

/** The provenance strip under the navigation, on every page, in plain words: a page that
 * shows scored data sets what it comes from; a page of stored or written facts sets a plain
 * line; any other page says what the app is built on. */
type PageProvenance = ProvenanceData | string | null;
const Ctx = createContext<(p: PageProvenance) => void>(() => {});

export function PageProvenanceProvider({ children }: { children: (p: PageProvenance)
                                                                    => ReactNode }) {
  const [p, setP] = useState<PageProvenance>(null);
  return <Ctx.Provider value={setP}>{children(p)}</Ctx.Provider>;
}

/** Set this page's provenance while it is shown. */
export function usePageProvenance(p: PageProvenance | undefined) {
  const set = useContext(Ctx);
  const key = JSON.stringify(p ?? null);
  useEffect(() => {
    set(p ?? null);
    return () => set(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, set]);
}

export function PageProvenanceBar({ p }: { p: PageProvenance }) {
  return (
    <div className="page-provenance" data-testid="page-provenance">
      {typeof p === "string" ? <p className="prov-line prov-plain">{p}</p>
        : p ? <ProvenanceStrip p={p} />
        : <p className="prov-line prov-plain">Public CIRA pump data (Zenodo) · read-only ·{" "}
            <Link to="/assumptions">assumptions register</Link></p>}
    </div>
  );
}
