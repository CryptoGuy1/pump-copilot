import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ProvenanceStrip, type ProvenanceData } from "./components/ui";

/** The provenance strip under the navigation, on every page: a page that shows scored data
 * sets what it comes from; any other page says what the whole app is built on. */
const Ctx = createContext<(p: ProvenanceData | null) => void>(() => {});

export function PageProvenanceProvider({ children }: { children: (p: ProvenanceData | null)
                                                                    => ReactNode }) {
  const [p, setP] = useState<ProvenanceData | null>(null);
  return <Ctx.Provider value={setP}>{children(p)}</Ctx.Provider>;
}

/** Set this page's provenance while it is shown. */
export function usePageProvenance(p: ProvenanceData | null | undefined) {
  const set = useContext(Ctx);
  const key = JSON.stringify(p ?? null);
  useEffect(() => {
    set(p ?? null);
    return () => set(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, set]);
}

export function PageProvenanceBar({ p }: { p: ProvenanceData | null }) {
  return (
    <div className="page-provenance" data-testid="page-provenance">
      {p ? <ProvenanceStrip p={p} label="This page" />
        : <div className="provenance-strip"><span className="label">Provenance</span>
            <span>public CIRA pump data (Zenodo), read-only; no control actions</span>
            <Link to="/assumptions">assumptions register</Link></div>}
    </div>
  );
}
