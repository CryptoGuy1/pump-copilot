/** The design system's components (Step 5b stage 1); the gallery at /design shows them all. */
export { STATES, StateBadge, StateIcon, StateMark, Synthetic, stateLabel } from "./state";
export { AssumptionChip, ProvenanceLine, ProvenanceStrip, type ProvenanceData,
         provenanceSentence, useAssumptions } from "./provenance";
export { SignalName, displayUnit, sig3, useSignalFormat, useSignalLabel, useSignalNames }
  from "./signals";
export { Card, Table, ValueReadout } from "./data";
export { Button, SelectField, Tabs, TextAreaField, TextField, type Tab } from "./controls";
export { EmptyState, ErrorPanel, Loading, NotInSnapshot, Skeleton } from "./feedback";
export { RepoCommit, RepoFile, useAbout } from "./links";
export { Actions } from "./actions";
