import type { CitationResponse, EligibilityGroups, ReplyKind } from "../api/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  kind?: ReplyKind;
  audioUrl?: string | null;
  eligibility?: EligibilityGroups;
  citations?: CitationResponse[];
  pending?: boolean;
}
