import { useState } from "react";
import { enc } from "../../shared/api";
import { ActionButton } from "../../shared/components/ActionButton";
import { Button } from "../../shared/components/Button";
import { Dialog, useConfirm } from "../../shared/components/Dialog";
import {
  Badge,
  Card,
  CopyButton,
  EmptyState,
  ErrorNote,
  GateList,
  RiskFlags,
  Skeleton,
  StatusBadge,
  Stepper,
} from "../../shared/components/Display";
import { CheckboxGroup, FormError, SelectField, TextField } from "../../shared/components/Field";
import { LegalRequirements } from "../../shared/components/Legal";
import { str, useFormSubmit } from "../../shared/components/useFormSubmit";
import { DOCUMENT_CATEGORIES, formatDate, formatDateTime, humanize, relativeTime } from "../../shared/format";
import { usePolling, useResource } from "../../shared/hooks";
import type { AuditChain, AuditEvent, LegacyOverview, Nominee, ReleaseRequest } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

const GATES = [
  "Invite code",
  "Email OTP",
  "Phone OTP",
  "Authenticator MFA",
  "ID match + review",
  "Death certificate review",
  "k-of-n confirmations",
  "Owner proof-of-life alert",
  "Cooling-off period",
  "Fraud risk check",
  "Country legal documents",
];

export function PlanSection() {
  const { caseId, version } = useCase();
  const legacy = useResource((s) => api.get<LegacyOverview>(casePath(caseId, "/legacy"), s), [caseId, version]);
  const audit = useResource(
    (s) => api.get<{ chain: AuditChain; events: AuditEvent[] }>(casePath(caseId, "/legacy/audit"), s),
    [caseId, version],
  );
  // nominees act in their own portal; keep this view current
  usePolling(() => {
    void legacy.reload();
    void audit.reload();
  }, 30_000);
  const [invite, setInvite] = useState<{ name: string; code: string } | null>(null);
  const [adding, setAdding] = useState(false);

  if (legacy.error) return <ErrorNote message={legacy.error} onRetry={legacy.reload} />;
  const data = legacy.data;

  return (
    <div className="stack">
      <section aria-labelledby="gates-heading">
        <h2 id="gates-heading" className="sr-only">
          Release gates
        </h2>
        <p className="muted">After a death is reported, the vault opens only when all of these pass:</p>
        <ol className="gate-strip">
          {GATES.map((gate) => (
            <li key={gate}>{gate}</li>
          ))}
        </ol>
      </section>

      {!data ? (
        <Skeleton lines={6} label="Loading plan" />
      ) : (
        <>
          <div className="grid grid-2">
            <PolicyCard data={data} />
            <Card
              title="Nominees & verification"
              description={`${data.verified_nominees} of ${data.nominees.length} fully verified. Nominees use the separate nominee portal.`}
              actions={
                <Button variant="primary" onClick={() => setAdding(true)}>
                  + Add nominee
                </Button>
              }
            >
              {data.nominees.length === 0 ? (
                <EmptyState title="No nominees yet">Add the people who may receive this vault.</EmptyState>
              ) : (
                <ul className="item-list">
                  {data.nominees.map((n) => (
                    <NomineeItem key={n.id} nominee={n} onInvite={(code) => setInvite({ name: n.full_name, code })} />
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card title="Death claims & release requests" description="Opened by a verified nominee. Review the certificate and watch the gates.">
            {data.requests.length === 0 ? (
              <EmptyState title="No death claim has been opened" />
            ) : (
              <ul className="item-list">
                {data.requests.map((r) => (
                  <RequestItem key={r.id} request={r} />
                ))}
              </ul>
            )}
          </Card>

          <div className="grid grid-2">
            <Card
              title={
                <>
                  Alerts sent {data.dev_outbox && <Badge tone="neutral">dev outbox</Badge>}
                </>
              }
              description={data.dev_outbox ? "Simulated SMS/email in development. Nominee codes are redacted, so you can never verify on their behalf." : "Delivery status of alerts sent to you and your nominees."}
            >
              {data.outbox.length === 0 ? (
                <p className="muted">Nothing sent yet.</p>
              ) : (
                <ul className="item-list">
                  {data.outbox.slice(0, 15).map((m, i) => (
                    <li key={i} className="item">
                      <div className="item-head">
                        <span className="item-title">
                          {humanize(m.channel)} → {m.destination_masked}
                        </span>
                        <time className="muted" dateTime={m.created_at}>
                          {relativeTime(m.created_at)}
                        </time>
                      </div>
                      <p className="item-meta">{m.body}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <AuditCard chain={audit.data?.chain} events={audit.data?.events} />
          </div>
        </>
      )}

      <AddNomineeDialog
        open={adding}
        categories={data?.document_categories ?? []}
        onClose={() => setAdding(false)}
        onAdded={(name, code) => {
          setAdding(false);
          setInvite({ name, code });
        }}
      />
      <Dialog
        open={invite !== null}
        onClose={() => setInvite(null)}
        title={`Invite code for ${invite?.name ?? ""}`}
        description="Shown only once. Share it in person or over a channel other than their registered email."
      >
        {invite && (
          <div className="stack">
            <code className="secret">{invite.code}</code>
            <div className="button-row">
              <CopyButton text={invite.code} label="Copy code" />
            </div>
            <ul className="hint">
              <li>Expires in 7 days.</li>
              <li>Works only with the email address you registered for them.</li>
              <li>They'll then verify email, phone, an authenticator app and their ID.</li>
            </ul>
            <div className="button-row end">
              <Button variant="primary" onClick={() => setInvite(null)}>
                I've saved it
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}

function PolicyCard({ data }: { data: LegacyOverview }) {
  const { caseId, changed } = useCase();
  const p = data.policy;
  const form = useFormSubmit(
    async (fd) => {
      const body: Record<string, unknown> = {
        quorum: Number(fd.get("quorum")),
        waiting_period_hours: Number(fd.get("waiting_period_hours")),
        checkin_interval_days: Number(fd.get("checkin_interval_days")),
        legal_documents: str(fd, "legal_documents"),
      };
      if (str(fd, "owner_email")) body.owner_email = str(fd, "owner_email");
      if (str(fd, "owner_phone")) body.owner_phone = str(fd, "owner_phone");
      await api.put(casePath(caseId, "/legacy/policy"), body);
      changed();
      return "Release policy saved.";
    },
    { reset: false },
  );
  return (
    <Card title="Release policy" description="How many confirmations, how long to wait, and how you prove you're alive.">
      <form className="stack" onSubmit={form.onSubmit} key={JSON.stringify(p)}>
        <FormError message={form.error} />
        <div className="form-row">
          <TextField
            label="Confirmations needed"
            hint="k of n nominees"
            name="quorum"
            type="number"
            min={1}
            max={10}
            defaultValue={p.quorum}
            required
          />
          <TextField
            label="Cooling-off (hours)"
            name="waiting_period_hours"
            type="number"
            hint={p.minimums?.waiting_period_hours ? `At least ${p.minimums.waiting_period_hours}h here` : `Recommended ${p.recommended?.waiting_period_hours ?? 72}h`}
            min={p.minimums?.waiting_period_hours ?? 0}
            max={2160}
            defaultValue={p.waiting_period_hours}
            required
          />
          <TextField
            label="Check-in every (days)"
            name="checkin_interval_days"
            type="number"
            min={7}
            max={730}
            defaultValue={p.checkin_interval_days}
            required
          />
        </div>
        <SelectField
          label="Country legal documents"
          name="legal_documents"
          defaultValue={p.legal_documents ?? "required"}
          hint="Required: a reviewer must approve the country's proof of entitlement (e.g. probate or a legal-heir certificate) before release."
          options={[
            { value: "required", label: "Required before release" },
            { value: "advisory", label: "Advisory (shown, not blocking)" },
          ]}
        />
        <div className="form-row">
          <TextField
            label="Your alert email"
            name="owner_email"
            type="email"
            autoComplete="email"
            optional
            hint={p.owner_email ? `Current: ${p.owner_email}` : "Not set"}
          />
          <TextField
            label="Your alert phone"
            name="owner_phone"
            type="tel"
            autoComplete="tel"
            optional
            placeholder="+91…"
            hint={p.owner_phone ? `Current: ${p.owner_phone}` : "Not set"}
          />
        </div>
        <div className="button-row">
          <Button type="submit" busy={form.busy}>
            Save policy
          </Button>
        </div>
      </form>
      <div className="callout" style={{ marginTop: "1rem" }}>
        <p>
          <strong>Last check-in:</strong>{" "}
          {p.last_owner_checkin ? (
            <time dateTime={p.last_owner_checkin} title={formatDateTime(p.last_owner_checkin)}>
              {relativeTime(p.last_owner_checkin)}
            </time>
          ) : (
            "never"
          )}
          . Checking in records proof of life and immediately cancels any pending death claim.
        </p>
        <ActionButton
          variant="primary"
          onAction={async () => {
            const result = await api.post<{ vetoed_requests?: number }>(casePath(caseId, "/legacy/checkin"));
            changed();
            return result.vetoed_requests
              ? `Checked in. ${result.vetoed_requests} pending death claim(s) cancelled.`
              : "Checked in. Proof of life recorded.";
          }}
        >
          I'm alive — check in
        </ActionButton>
      </div>
    </Card>
  );
}

function NomineeItem({ nominee: n, onInvite }: { nominee: Nominee; onInvite: (code: string) => void }) {
  const { caseId, changed } = useCase();
  const confirm = useConfirm();
  const base = `/legacy/nominees/${enc(n.id)}`;
  const review = (decision: "approve" | "reject") => async () => {
    await api.post(casePath(caseId, `${base}/identity-review`), { decision });
    changed();
    return `Identity ${decision === "approve" ? "approved" : "rejected"} for ${n.full_name}.`;
  };
  return (
    <li className="item">
      <div className="item-head">
        <span className="item-title">{n.full_name}</span>
        <span className="button-row">
          <Badge tone="neutral">{humanize(n.role)}</Badge>
          <StatusBadge status={n.verified ? "verified" : n.status} />
          {n.locked && <StatusBadge status="locked" />}
        </span>
      </div>
      <p className="item-meta">
        {humanize(n.relationship)} · {n.email} · {n.phone}
      </p>
      <p className="item-meta">
        May see: {n.role === "executor" ? "everything" : n.allowed_categories.map((c) => DOCUMENT_CATEGORIES[c] ?? humanize(c)).join(", ") || "nothing"}
      </p>
      <div style={{ marginTop: "0.6rem" }}>
        <Stepper steps={n.steps} label={`Verification progress for ${n.full_name}`} compact />
      </div>
      {n.status !== "revoked" && (
        <div className="item-actions">
          {n.identity_status === "pending_review" && (
            <>
              <ActionButton size="sm" variant="primary" onAction={review("approve")} aria-label={`Approve ID for ${n.full_name}`}>
                Approve ID
              </ActionButton>
              <ActionButton size="sm" variant="danger" onAction={review("reject")} aria-label={`Reject ID for ${n.full_name}`}>
                Reject ID
              </ActionButton>
            </>
          )}
          {!n.steps[0]?.done && (
            <ActionButton
              size="sm"
              onAction={async () => {
                const result = await api.post<{ invite_code: string }>(casePath(caseId, `${base}/reinvite`));
                onInvite(result.invite_code);
              }}
              aria-label={`New invite code for ${n.full_name}`}
            >
              New invite code
            </ActionButton>
          )}
          <ActionButton
            size="sm"
            variant="danger"
            aria-label={`Revoke ${n.full_name}`}
            onAction={async () => {
              const ok = await confirm({
                title: `Revoke ${n.full_name}?`,
                message: "They are signed out immediately and can no longer verify, confirm a death or see the vault.",
                confirmLabel: "Revoke access",
                danger: true,
              });
              if (!ok) return;
              await api.post(casePath(caseId, `${base}/revoke`));
              changed();
              return `${n.full_name} revoked and signed out.`;
            }}
          >
            Revoke
          </ActionButton>
        </div>
      )}
    </li>
  );
}

function RequestItem({ request: r }: { request: ReleaseRequest }) {
  const { caseId, changed } = useCase();
  const [note, setNote] = useState("");
  const base = `/legacy/requests/${enc(r.id)}`;
  const decide = (decision: "approve" | "reject") => async () => {
    const body: Record<string, string> = { decision };
    if (note.trim()) body.risk_override_note = note.trim();
    const result = await api.post<{ status: string }>(casePath(caseId, `${base}/certificate-review`), body);
    changed();
    return `Certificate ${decision}d. Request is now ${humanize(result.status)}.`;
  };
  const noteId = `note-${r.id}`;
  const reviewLegal = (legalId: string, decision: "approve" | "reject") => async () => {
    const result = await api.post<{ status: string }>(
      casePath(caseId, `${base}/legal-documents/${enc(legalId)}/review`),
      { decision },
    );
    changed();
    return `Legal document ${decision}d. Request is ${humanize(result.status)}.`;
  };
  return (
    <li className="item">
      <div className="item-head">
        <span className="item-title">Request opened {formatDate(r.opened_at)}</span>
        <span className="button-row">
          <StatusBadge status={r.status} />
          <Badge tone={r.risk.level === "high" ? "danger" : r.risk.level === "medium" ? "warning" : "info"}>
            Risk {r.risk.level} ({r.risk.score})
          </Badge>
        </span>
      </div>
      <p className="item-meta">
        Claimed date of death: {formatDate(r.date_of_death)} · Confirmations:{" "}
        {r.attestations.length ? r.attestations.map((a) => `${a.full_name} (${a.decision})`).join(", ") : "none yet"}
      </p>
      {r.close_reason && <p className="item-meta">{r.close_reason}</p>}
      <div className="grid grid-2" style={{ marginTop: "0.75rem" }}>
        <div>
          <h3>Gates</h3>
          <GateList gates={r.gates} />
        </div>
        <div>
          <h3>Fraud signals</h3>
          <RiskFlags flags={r.risk.flags} />
        </div>
      </div>
      {r.legal && (
        <div style={{ marginTop: "0.75rem" }}>
          <LegalRequirements
            legal={r.legal}
            documentActions={(doc) => (
              <div className="item-actions">
                {doc.document_id && (
                  <a className="btn btn-secondary btn-sm" href={casePath(caseId, `/documents/${enc(doc.document_id)}/download`)}>
                    Download<span className="sr-only"> {doc.document_type}</span>
                  </a>
                )}
                {doc.status === "pending_review" && (r.status === "open" || r.status === "frozen") && (
                  <>
                    <ActionButton size="sm" variant="primary" onAction={reviewLegal(doc.id, "approve")} aria-label={`Approve ${doc.document_type}`}>
                      Approve
                    </ActionButton>
                    <ActionButton size="sm" variant="danger" onAction={reviewLegal(doc.id, "reject")} aria-label={`Reject ${doc.document_type}`}>
                      Reject
                    </ActionButton>
                  </>
                )}
              </div>
            )}
          />
        </div>
      )}
      {r.certificate_document_id && (
        <p style={{ marginTop: "0.75rem" }}>
          <a href={casePath(caseId, `/documents/${enc(r.certificate_document_id)}/download`)}>Download the death certificate to review</a>
        </p>
      )}
      {(r.status === "open" || r.status === "frozen") && (
        <div className="stack-sm" style={{ marginTop: "0.75rem" }}>
          <div className="field">
            <label htmlFor={noteId}>
              Risk override note <span className="opt">(required only to accept a HIGH risk)</span>
            </label>
            <input id={noteId} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="button-row">
            <ActionButton variant="primary" onAction={decide("approve")}>
              Approve certificate
            </ActionButton>
            <ActionButton variant="danger" onAction={decide("reject")}>
              Reject certificate
            </ActionButton>
            <ActionButton
              onAction={async () => {
                const result = await api.post<{ status: string }>(casePath(caseId, `${base}/evaluate`));
                changed();
                return `Request is ${humanize(result.status)}.`;
              }}
            >
              Re-check gates
            </ActionButton>
          </div>
        </div>
      )}
    </li>
  );
}

function AuditCard({ chain, events }: { chain?: AuditChain; events?: AuditEvent[] }) {
  const [showAll, setShowAll] = useState(false);
  const rows = events ? (showAll ? events : events.slice(0, 10)) : [];
  return (
    <Card title="Tamper-evident audit trail" description="Every release action is hash-chained; editing any record breaks the chain.">
      {!chain || !events ? (
        <Skeleton />
      ) : (
        <>
          <div className={`callout ${chain.valid ? "callout-success" : "callout-danger"}`} role={chain.valid ? undefined : "alert"}>
            <p>
              <span aria-hidden="true">{chain.valid ? "✓ " : "⚠ "}</span>
              {chain.valid
                ? `Hash chain intact · ${chain.events} event(s)${chain.head ? ` · head ${chain.head.slice(0, 12)}…` : ""}`
                : `Audit chain broken at event ${chain.broken_at_seq}. Records were altered.`}
            </p>
          </div>
          {events.length > 0 && (
            <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
              <table>
                <caption className="sr-only">Audit events, newest first</caption>
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">When</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Action</th>
                    <th scope="col">Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <tr key={e.seq}>
                      <td>{e.seq}</td>
                      <td>
                        <time dateTime={e.created_at}>{formatDateTime(e.created_at)}</time>
                      </td>
                      <td>{e.actor}</td>
                      <td>{humanize(e.action.replace(/\./g, " "))}</td>
                      <td>
                        <code>{e.hash.slice(0, 10)}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {events.length > 10 && (
            <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
              {showAll ? "Show fewer" : `Show all ${events.length} events`}
            </Button>
          )}
        </>
      )}
    </Card>
  );
}

function AddNomineeDialog({
  open,
  categories,
  onClose,
  onAdded,
}: {
  open: boolean;
  categories: string[];
  onClose: () => void;
  onAdded: (name: string, code: string) => void;
}) {
  const { caseId, changed } = useCase();
  const keys = Array.from(new Set([...Object.keys(DOCUMENT_CATEGORIES), ...categories])).filter(
    (k) => k !== "death_certificate" && k !== "identity_document",
  );
  const form = useFormSubmit(async (fd) => {
    const body: Record<string, unknown> = {
      full_name: str(fd, "full_name"),
      relationship: str(fd, "relationship"),
      role: str(fd, "role"),
      email: str(fd, "email"),
      phone: str(fd, "phone"),
      allowed_categories: fd.getAll("allowed_categories").map(String),
    };
    if (str(fd, "id_last4")) body.id_last4 = str(fd, "id_last4");
    if (str(fd, "date_of_birth")) body.date_of_birth = str(fd, "date_of_birth");
    const result = await api.post<{ full_name: string; invite_code: string }>(casePath(caseId, "/legacy/nominees"), body);
    changed();
    onAdded(result.full_name, result.invite_code);
    return `${result.full_name} added. Share the invite code securely.`;
  });
  return (
    <Dialog open={open} onClose={onClose} title="Add a nominee or beneficiary" size="lg">
      <form className="stack" onSubmit={form.onSubmit}>
        <FormError message={form.error} />
        <div className="form-row">
          <TextField label="Full name" name="full_name" required maxLength={200} autoComplete="off" />
          <TextField label="Relationship" name="relationship" required maxLength={100} placeholder="spouse, child…" autoComplete="off" />
        </div>
        <SelectField
          label="Role"
          name="role"
          options={[
            { value: "primary", label: "Primary beneficiary" },
            { value: "secondary", label: "Secondary beneficiary" },
            { value: "executor", label: "Executor (sees everything)" },
            { value: "guardian", label: "Guardian of a minor" },
            { value: "claimant", label: "Claimant (Discovery)" },
          ]}
        />
        <div className="form-row">
          <TextField label="Email they will verify" name="email" type="email" required autoComplete="off" />
          <TextField label="Phone they will verify" name="phone" type="tel" required autoComplete="off" placeholder="+91…" />
        </div>
        <div className="form-row">
          <TextField
            label="Last 4 characters of their ID"
            name="id_last4"
            maxLength={4}
            optional
            autoComplete="off"
            hint="Stored only as a keyed hash; used to match their ID later."
          />
          <TextField label="Their date of birth" name="date_of_birth" type="date" optional hint="Stored only as a keyed hash." />
        </div>
        <CheckboxGroup
          legend="Document types they may see after release"
          name="allowed_categories"
          columns={2}
          defaultChecked={["will"]}
          options={keys.map((k) => ({ value: k, label: DOCUMENT_CATEGORIES[k] ?? humanize(k) }))}
          hint="Executors always see everything."
        />
        <div className="button-row end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={form.busy}>
            Add and create invite code
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
