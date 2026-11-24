import { createContext, useContext } from "react";
import type { CaseItem } from "../shared/types";

export interface CaseContextValue {
  caseId: string;
  caseItem: CaseItem;
  /** Increments whenever any section changes case data, so other sections refetch. */
  version: number;
  /** Tell the workspace something changed (refreshes counts and dependent sections). */
  changed: () => void;
  /** Navigate to another section. */
  go: (section: SectionKey) => void;
}

export const SECTIONS = [
  "overview",
  "plan",
  "discovery",
  "documents",
  "people",
  "claims",
  "graph",
  "compliance",
  "assistant",
] as const;
export type SectionKey = (typeof SECTIONS)[number];

export const CaseContext = createContext<CaseContextValue | null>(null);

export function useCase(): CaseContextValue {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCase must be used inside a selected case");
  return value;
}
