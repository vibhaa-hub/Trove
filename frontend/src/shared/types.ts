/** Response shapes used by the UI. Only the fields the UI reads are listed. */

export type Mode = "plan" | "discovery";
export type CountryCode = "IN" | "UK" | "US" | "EU" | "OTHER";

export interface AuthStatus {
  setup_required: boolean;
  signup_allowed: boolean;
  authenticated: boolean;
  user?: { id: string; email?: string | null; role?: string } | null;
}

export interface CaseItem {
  id: string;
  mode: Mode;
  subject_name: string;
  jurisdiction: string;
  country?: CountryCode;
  stage?: string;
  created_at?: string;
}

export interface Workflow {
  stage: string;
  mode: Mode;
  asset_count: number;
  assets_needing_review: number;
  death_status: string;
  unverified_beneficiaries: number;
  can_advance: boolean;
}

export interface Asset {
  id: string;
  asset_type: string;
  status: string;
  institution?: string | null;
  masked_reference?: string | null;
  description?: string | null;
}

export interface DocumentItem {
  id: string;
  file_name: string;
  category: string;
  review_status: string;
  sha256: string;
  created_at?: string;
}

export interface Beneficiary {
  id: string;
  name: string;
  relationship: string;
  verification_status: string;
  beneficiary_type: string;
  share_percent?: number | null;
}

export interface Claim {
  id: string;
  institution?: string | null;
  status: string;
  next_action?: string | null;
  due_date?: string | null;
}

export interface Person {
  full_name: string;
}

export interface EntityMatch {
  person_a: Person;
  person_b: Person;
  similarity: number;
}

export interface RelationshipGraph {
  nodes: { id: string; label: string; type: string; status?: string }[];
  edges: { source: string; target: string; label: string; status: string }[];
}

/* ------------------------------------------------------------------ Plan */
export interface Step {
  key: string;
  label: string;
  done: boolean;
  state?: string;
}

export interface Nominee {
  id: string;
  full_name: string;
  relationship: string;
  role: string;
  email: string;
  phone: string;
  status: string;
  verified: boolean;
  locked?: boolean;
  identity_status: string;
  allowed_categories: string[];
  steps: Step[];
}

export interface Gate {
  key?: string;
  label: string;
  passed: boolean;
}

export interface RiskFlag {
  level: "low" | "medium" | "high" | string;
  message: string;
}

export interface Attestation {
  full_name: string;
  decision: string;
}

export interface ReleaseRequest {
  id: string;
  status: string;
  date_of_death: string;
  opened_at: string;
  close_reason?: string | null;
  certificate_document_id?: string | null;
  attestations: Attestation[];
  gates: Gate[];
  risk: { level: string; score: number; flags: RiskFlag[] };
  you_attested?: boolean;
  legal?: LegalStatus;
}

export interface LegalRequirementStatus {
  key: string;
  label: string;
  why: string;
  accepted: string[];
  satisfied: boolean;
  pending: boolean;
}

export interface LegalDocument {
  id: string;
  requirement_key: string;
  document_type: string;
  status: string;
  document_id?: string;
  file_name?: string | null;
  created_at: string;
}

export interface LegalStatus {
  mode: "required" | "advisory";
  country: CountryCode;
  country_name: string;
  requirements: LegalRequirementStatus[];
  documents: LegalDocument[];
  satisfied: number;
  total: number;
}

/* ------------------------------------------------------- Country rules */
export interface CountrySummary {
  code: CountryCode;
  name: string;
  data_region: string;
  states_label: string;
  examples: string[];
}

export interface JurisdictionCatalog {
  countries: CountrySummary[];
  data_region: string | null;
  enforce_residency: boolean;
  disclaimer: string;
}

export interface CountryProfile {
  code: CountryCode;
  name: string;
  succession: string;
  legal_requirements: { key: string; label: string; why: string; accepted: string[] }[];
  legal_documents_default: "required" | "advisory";
  defaults: Record<string, number>;
  minimums: Record<string, number>;
  privacy: {
    law: string;
    regulator: string;
    rights: string[];
    retention: string;
    transfers: string;
    deceased_data: string;
    notice: string;
    contact_requirement: string;
  };
  official_tracing: Record<string, string[]>;
  data_region: string;
  residency_note: string;
  states_label: string;
  last_reviewed: string;
  disclaimer: string;
}

export interface Compliance {
  country: CountryCode;
  jurisdiction: string;
  profile: CountryProfile;
  legal_documents_mode: "required" | "advisory";
  date_of_death: string | null;
  deadlines: { label: string; detail: string; due: string | null; overdue: boolean; days_left: number | null }[];
  data_residency: {
    deployment_region: string | null;
    recommended_region: string;
    enforced: boolean;
    matches: boolean;
    note: string;
  };
}

export interface Policy {
  quorum: number;
  waiting_period_hours: number;
  checkin_interval_days: number;
  owner_email?: string | null;
  owner_phone?: string | null;
  last_owner_checkin?: string | null;
  legal_documents?: "required" | "advisory";
  country?: CountryCode;
  minimums?: Record<string, number>;
  recommended?: Record<string, number>;
}

export interface OutboxMessage {
  created_at: string;
  channel: string;
  destination_masked: string;
  body: string;
}

export interface LegacyOverview {
  policy: Policy;
  nominees: Nominee[];
  requests: ReleaseRequest[];
  outbox: OutboxMessage[];
  verified_nominees: number;
  document_categories: string[];
  dev_outbox?: boolean;
}

export interface AuditEvent {
  seq: number;
  created_at: string;
  actor: string;
  action: string;
  hash: string;
}

export interface AuditChain {
  valid: boolean;
  events: number;
  head?: string | null;
  broken_at_seq?: number | null;
}

/* ------------------------------------------------------------- Discovery */
export interface Lead {
  id: string;
  status: "new" | "dismissed" | "promoted" | string;
  category_label: string;
  asset_type: string;
  institution?: string | null;
  masked_refs: string[];
  evidence: string[];
  source_count: number;
  source_types: string[];
  confidence: number;
  next_step: string;
}

export interface PlanStep {
  priority: string;
  title: string;
  why: string;
  action: string;
}

export interface Coverage {
  label: string;
  status: "found" | "gap" | string;
  leads: number;
  where_to_look: string;
  official_tracing: string[];
}

export interface DiscoveryOverview {
  have: string[];
  privacy: string;
  sources: { source_type: string; label: string; items_scanned: number; signals_found: number }[];
  plan: PlanStep[];
  leads: Lead[];
  coverage: Coverage[];
  access_recovery: { title: string; steps: string[] }[];
  scan?: { items_scanned: number; signals_found: number };
}

export interface Connector {
  id: string;
  name: string;
  status: "available" | "planned" | string;
  needs?: string[];
  stores_credentials?: boolean;
}

export interface KgNode {
  key: string;
  kind: string;
  label: string;
  props: Record<string, unknown>;
}

export interface KgEdge {
  src: string;
  dst: string;
  rel: string;
}

export interface Insight {
  type: string;
  severity: "high" | "medium" | "low" | "info" | string;
  message: string;
}

export interface KnowledgeGraph {
  nodes: KgNode[];
  edges: KgEdge[];
  insights: Insight[];
}

export interface AssistantReply {
  answer: string;
  llm_used: boolean;
  note?: string;
  disclaimer?: string;
  trace?: { skills_loaded?: string[]; tools_called?: string[]; resources_read?: string[] };
}

export interface MockEmail {
  id: string;
  date: string;
  subject: string;
  from: string;
}

/* --------------------------------------------------------------- Nominee */
export interface NomineeMe {
  session_level: string;
  case: { id?: string; subject_name: string; country?: CountryCode };
  nominee: Nominee & { identity_hint_registered: boolean };
  requests: ReleaseRequest[];
  released: boolean;
  quorum: number;
  waiting_period_hours: number;
}

export interface VaultView {
  full_access: boolean;
  documents: { id: string; file_name: string; category: string }[];
  assets: Asset[];
  beneficiaries: Beneficiary[];
}

/* -------------------------------------------------------------- Reviewer */
export interface ReviewerQueue {
  reviewer: { username: string; role: string };
  identity_reviews: {
    case_id: string;
    nominee_id: string;
    full_name: string;
    relationship: string;
    role: string;
    subject_name: string;
    mode: string;
    hint_matched: boolean;
    identity_document_id?: string | null;
    relationship_document_id?: string | null;
  }[];
  certificate_reviews: (ReleaseRequest & { case_id: string; subject_name: string })[];
  legal_document_reviews: {
    id: string;
    request_id: string;
    case_id: string;
    requirement_key: string;
    requirement_label: string;
    document_type: string;
    document_id: string;
    created_at: string;
    subject_name: string;
    country_name: string;
    uploaded_by: string;
  }[];
}
