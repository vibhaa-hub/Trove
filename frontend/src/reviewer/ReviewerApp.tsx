import { useCallback, useEffect, useState } from "react";
import { ApiError, createClient, enc, errorMessage } from "../shared/api";
import { ActionButton } from "../shared/components/ActionButton";
import { Badge, Card, EmptyState, GateList, RiskFlags, Skeleton } from "../shared/components/Display";
import { Button } from "../shared/components/Button";
import { CodeField, FormError, PasswordField, TextField } from "../shared/components/Field";
import { AppHeader, Footer, PageHeading } from "../shared/components/Layout";
import { Tabs } from "../shared/components/Tabs";
import { useToast } from "../shared/components/Toaster";
import { str, useFormSubmit } from "../shared/components/useFormSubmit";
import { formatDate, humanize } from "../shared/format";
import { useDocumentTitle, usePolling } from "../shared/hooks";
import type { ReviewerQueue } from "../shared/types";

const SIGNED_OUT = "trove:reviewer-unauthorized";
const api = createClient("/api/reviewer", () => window.dispatchEvent(new Event(SIGNED_OUT)), ["/queue"]);

export function ReviewerApp() {
  const toast = useToast();
  const [queue, setQueue] = useState<ReviewerQueue | null | undefined>(undefined);
  const [tab, setTab] = useState<"identity" | "certificates" | "legal">("identity");

  const load = useCallback(async () => {
    try {
      setQueue(await api.get<ReviewerQueue>("/queue"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setQueue(null);
      else toast.error(errorMessage(error));
    }
  }, [toast]);

  useEffect(() => {
    void load();
    const out = () => setQueue(null);
    window.addEventListener(SIGNED_OUT, out);
    return () => window.removeEventListener(SIGNED_OUT, out);
  }, [load]);
  usePolling(() => void load(), 30_000, Boolean(queue));

  const total = queue
    ? queue.identity_reviews.length + queue.certificate_reviews.length + (queue.legal_document_reviews?.length ?? 0)
    : 0;
  useDocumentTitle(queue ? `(${total}) Review queue · Trove` : "Reviewer console · Trove");

  const login = useFormSubmit(async (fd) => {
    await api.post("/login", { username: str(fd, "username"), password: String(fd.get("password")), totp: str(fd, "totp") });
    await load();
    return "Signed in.";
  });

  return (
    <>
      <AppHeader area="Reviewer console" home="/reviewer">
        {queue && (
          <>
            <span className="user-chip">
              {queue.reviewer.username} · {humanize(queue.reviewer.role)}
            </span>
            <Button
              size="sm"
              onClick={async () => {
                await api.post("/logout").catch(() => undefined);
                setQueue(null);
              }}
            >
              Sign out
            </Button>
          </>
        )}
      </AppHeader>
      <main id="main" className="portal" tabIndex={-1}>
        {queue === undefined ? (
          <Skeleton lines={4} label="Checking your session" />
        ) : queue === null ? (
          <div className="auth-wrap">
            <section className="card auth-card" aria-labelledby="rev-title">
              <h1 id="rev-title">Reviewer sign-in</h1>
              <p className="muted">Independent Trove staff only. Separate from the account owner; authenticator code required.</p>
              <form className="stack" onSubmit={login.onSubmit}>
                <FormError message={login.error} />
                <TextField label="Username" name="username" required autoComplete="username" />
                <PasswordField label="Password" name="password" required />
                <CodeField label="Authenticator code" name="totp" required />
                <Button type="submit" variant="primary" block busy={login.busy}>
                  Sign in
                </Button>
              </form>
            </section>
          </div>
        ) : (
          <>
            <PageHeading lede="Approve only what the documents clearly support. Every decision is recorded in the case's tamper-evident audit trail.">
              Review queue
            </PageHeading>
            <Tabs
              label="Review types"
              active={tab}
              onChange={setTab}
              items={[
                {
                  key: "identity",
                  label: "Identity",
                  badge: queue.identity_reviews.length,
                  panel: <IdentityQueue queue={queue} reload={load} />,
                },
                {
                  key: "certificates",
                  label: "Death certificates",
                  badge: queue.certificate_reviews.length,
                  panel: <CertificateQueue queue={queue} reload={load} />,
                },
                {
                  key: "legal",
                  label: "Legal documents",
                  badge: queue.legal_document_reviews?.length ?? 0,
                  panel: <LegalQueue queue={queue} reload={load} />,
                },
              ]}
            />
          </>
        )}
      </main>
      <Footer />
    </>
  );
}

const docHref = (caseId: string, docId: string) => `/api/reviewer/cases/${enc(caseId)}/documents/${enc(docId)}`;

function IdentityQueue({ queue, reload }: { queue: ReviewerQueue; reload: () => Promise<void> }) {
  if (!queue.identity_reviews.length) return <EmptyState title="Nothing waiting">New identity submissions appear here automatically.</EmptyState>;
  return (
    <ul className="item-list">
      {queue.identity_reviews.map((item) => {
        const decide = (decision: "approve" | "reject") => async () => {
          await api.post(`/cases/${enc(item.case_id)}/nominees/${enc(item.nominee_id)}/identity-review`, { decision });
          await reload();
          return `${item.full_name}: identity ${decision === "approve" ? "approved" : "rejected"}.`;
        };
        return (
          <li key={item.nominee_id}>
            <Card title={item.full_name} level={2} actions={<Badge tone={item.hint_matched ? "success" : "warning"}>{item.hint_matched ? "ID hint matched" : "No ID hint — check relationship proof"}</Badge>}>
              <p className="item-meta">
                {humanize(item.relationship)} · {humanize(item.role)} · case of {item.subject_name} ({humanize(item.mode)})
              </p>
              <div className="item-actions">
                {item.identity_document_id && (
                  <a className="btn btn-secondary btn-sm" href={docHref(item.case_id, item.identity_document_id)}>
                    Open ID document<span className="sr-only"> for {item.full_name}</span>
                  </a>
                )}
                {item.relationship_document_id && (
                  <a className="btn btn-secondary btn-sm" href={docHref(item.case_id, item.relationship_document_id)}>
                    Open relationship proof<span className="sr-only"> for {item.full_name}</span>
                  </a>
                )}
              </div>
              <div className="item-actions">
                <ActionButton variant="primary" onAction={decide("approve")} aria-label={`Approve identity of ${item.full_name}`}>
                  Approve
                </ActionButton>
                <ActionButton variant="danger" onAction={decide("reject")} aria-label={`Reject identity of ${item.full_name}`}>
                  Reject
                </ActionButton>
              </div>
            </Card>
          </li>
        );
      })}
    </ul>
  );
}

function CertificateQueue({ queue, reload }: { queue: ReviewerQueue; reload: () => Promise<void> }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!queue.certificate_reviews.length) return <EmptyState title="Nothing waiting">New death claims appear here automatically.</EmptyState>;
  return (
    <ul className="item-list">
      {queue.certificate_reviews.map((r) => {
        const note = notes[r.id] ?? "";
        const decide = (decision: "approve" | "reject") => async () => {
          await api.post(
            `/cases/${enc(r.case_id)}/requests/${enc(r.id)}/certificate-review`,
            note.trim() ? { decision, risk_override_note: note.trim() } : { decision },
          );
          await reload();
          return `Certificate ${decision}d.`;
        };
        const noteId = `override-${r.id}`;
        return (
          <li key={r.id}>
            <Card
              title={`${r.subject_name} · claimed death ${formatDate(r.date_of_death)}`}
              level={2}
              actions={<Badge tone={r.risk.level === "high" ? "danger" : r.risk.level === "medium" ? "warning" : "info"}>Risk {r.risk.level} ({r.risk.score})</Badge>}
            >
              <div className="grid grid-2">
                <div>
                  <h3>Fraud signals</h3>
                  <RiskFlags flags={r.risk.flags} />
                </div>
                <div>
                  <h3>Gates</h3>
                  <GateList gates={r.gates} />
                </div>
              </div>
              {r.certificate_document_id && (
                <div className="item-actions">
                  <a className="btn btn-secondary btn-sm" href={docHref(r.case_id, r.certificate_document_id)}>
                    Open death certificate
                  </a>
                </div>
              )}
              <div className="field" style={{ marginTop: "0.75rem" }}>
                <label htmlFor={noteId}>
                  Override reason <span className="opt">{r.risk.level === "high" ? "(required to approve a HIGH risk)" : "(optional)"}</span>
                </label>
                <input id={noteId} maxLength={500} value={note} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} />
              </div>
              <div className="item-actions">
                <ActionButton variant="primary" onAction={decide("approve")}>
                  Approve certificate
                </ActionButton>
                <ActionButton variant="danger" onAction={decide("reject")}>
                  Reject certificate
                </ActionButton>
              </div>
            </Card>
          </li>
        );
      })}
    </ul>
  );
}

function LegalQueue({ queue, reload }: { queue: ReviewerQueue; reload: () => Promise<void> }) {
  const items = queue.legal_document_reviews ?? [];
  if (!items.length) return <EmptyState title="Nothing waiting">Probate, succession and heirship documents appear here.</EmptyState>;
  return (
    <ul className="item-list">
      {items.map((item) => {
        const decide = (decision: "approve" | "reject") => async () => {
          await api.post(
            `/cases/${enc(item.case_id)}/requests/${enc(item.request_id)}/legal-documents/${enc(item.id)}/review`,
            { decision },
          );
          await reload();
          return `${item.document_type}: ${decision === "approve" ? "approved" : "rejected"}.`;
        };
        return (
          <li key={item.id}>
            <Card title={`${item.subject_name} · ${item.requirement_label}`} level={2} actions={<Badge tone="info">{item.country_name}</Badge>}>
              <p className="item-meta">
                {item.document_type} · uploaded by {item.uploaded_by} on {formatDate(item.created_at)}
              </p>
              <p className="hint">Check that the document is genuine, names the deceased, and names this nominee as an heir or representative.</p>
              <div className="item-actions">
                <a className="btn btn-secondary btn-sm" href={docHref(item.case_id, item.document_id)}>
                  Open document<span className="sr-only"> {item.document_type}</span>
                </a>
              </div>
              <div className="item-actions">
                <ActionButton variant="primary" onAction={decide("approve")} aria-label={`Approve ${item.document_type} for ${item.subject_name}`}>
                  Approve
                </ActionButton>
                <ActionButton variant="danger" onAction={decide("reject")} aria-label={`Reject ${item.document_type} for ${item.subject_name}`}>
                  Reject
                </ActionButton>
              </div>
            </Card>
          </li>
        );
      })}
    </ul>
  );
}
