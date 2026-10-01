import type { ReactNode } from "react";
import { ACTIONS_DISABLED, SNAPSHOT } from "../../snapshot";

/** Controls that change something (case actions, replay controls, new questions). In the
 * static snapshot they stay visible but disabled, with a note saying why; otherwise this adds
 * nothing. */
export function Actions({ children, note = true }: { children: ReactNode; note?: boolean }) {
  if (!SNAPSHOT) return <>{children}</>;
  return (
    <div className="snapshot-actions" data-testid="snapshot-actions">
      <fieldset disabled className="snapshot-fieldset">{children}</fieldset>
      {note && <p className="snapshot-note">{ACTIONS_DISABLED}</p>}
    </div>
  );
}
