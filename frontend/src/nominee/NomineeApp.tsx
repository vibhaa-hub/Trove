import { useCallback, useEffect, useState } from "react";
import { ApiError, takeFile, enc, errorMessage, fileToBase64 } from "../shared/api";
import { ActionButton } from "../shared/components/ActionButton";
import { Button } from "../shared/components/Button";
import { Badge, Card, Disclosure, EmptyState, GateList, Skeleton, StatusBadge, Stepper } from "../shared/components/Display";
import { CodeField, FileField, FormError, PasswordField, SelectField, TextField } from "../shared/components/Field";
import { LegalRequirements } from "../shared/components/Legal";
import { AppHeader, Footer, PageHeading } from "../shared/components/Layout";
import { Tabs } from "../shared/components/Tabs";
import { useToast } from "../shared/components/Toaster";
import { str, useFormSubmit } from "../shared/components/useFormSubmit";
import { formatDate, formatDateTime, humanize } from "../shared/format";
import { useDocumentTitle, usePolling, useResource } from "../shared/hooks";
import type { CountryProfile, NomineeMe, OutboxMessage, VaultView } from "../shared/types";
import { createClient } from "../shared/api";
import { api, NOMINEE_UNAUTHORIZED } from "./api";

type Me = NomineeMe;
const publicApi = createClient("");

export function NomineeApp() {
  const toast = useToast();
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>("/me"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setMe(null);
      else toast.error(errorMessage(error));
    }
  }, [toast]);

  useEffect(() => {
    void refresh();
    const signedOut = () => setMe(null);
    window.addEventListener(NOMINEE_UNAUTHORIZED, signedOut);
    return () => window.removeEventListener(NOMINEE_UNAUTHORIZED, signedOut);
  }, [refresh]);

  // other nominees and the reviewer act elsewhere; keep the journey current
  usePolling(() => void refresh(), 20_000, Boolean(me));
  useDocumentTitle(me ? `Your verification · Trove` : "Nominee portal · Trove");

  return (
    <>
      <AppHeader area="Nominee portal" home="/nominee">
        {me && (
          <>
            <Badge tone={me.session_level === "mfa" ? "success" : "warning"}>
              {me.session_level === "mfa" ? "Signed in · MFA" : "Signed in · MFA pending"}
            </Badge>
            <Button
              size="sm"
              onClick={async () => {
                await api.post("/logout").catch(() => undefined);
                setMe(null);
                toast.info("Signed out.");
              }}
            >
              Sign out
            </Button>
          </>
        )}
      </AppHeader>
      <main id="main" className="portal" tabIndex={-1}>
        {me === undefined ? (
          <Skeleton lines={5} label="Checking your session" />
        ) : me === null ? (
          <SignedOut onSignedIn={refresh} />
        ) : (
          <Journey me={me} refresh={refresh} />
        )}
      </main>
      <Footer />
    </>
  );
}

/* ----------------------------------------------------------- signed out */
function SignedOut({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
  const [tab, setTab] = useState<"invite" | "login">("invite");
  const enroll = useFormSubmit(async (fd) => {
    await api.post("/enroll", { invite_code: str(fd, "invite_code"), email: str(fd, "email"), password: String(fd.get("password")) });
    await onSignedIn();
    return "Invitation accepted. Next, verify your email and phone.";
  });
  const login = useFormSubmit(async (fd) => {
    const body: Record<string, string> = { email: str(fd, "email"), password: String(fd.get("password")) };
    if (str(fd, "totp")) body.totp = str(fd, "totp");
    if (str(fd, "case_id")) body.case_id = str(fd, "case_id");
    await api.post("/login", body);
    await onSignedIn();
    return "Signed in.";
  });
  return (
    <>
      <PageHeading lede="Someone named you as a nominee or beneficiary of their Trove legacy vault. Your access is protected by several checks so only you can claim it.">
        Nominee &amp; beneficiary portal
      </PageHeading>
      <Tabs
        label="Get started"
        active={tab}
        onChange={setTab}
        items={[
          {
            key: "invite",
            label: "I have an invite code",
            panel: (
              <Card title="Accept your invitation" description="The owner gave you a one-time code. It works only with the email address they registered for you.">
                <form className="stack" onSubmit={enroll.onSubmit}>
                  <FormError message={enroll.error} />
                  <TextField label="Invite code" name="invite_code" required autoComplete="off" spellCheck={false} placeholder="XXXX-XXXX-XXXX-XXXX" />
                  <TextField label="Your email" name="email" type="email" required autoComplete="email" />
                  <PasswordField label="Choose a password" name="password" required minLength={12} isNew />
                  <Button type="submit" variant="primary" block busy={enroll.busy}>
                    Accept invitation
                  </Button>
                </form>
              </Card>
            ),
          },
          {
            key: "login",
            label: "Sign in",
            panel: (
              <Card title="Sign in" description="Five wrong attempts lock the account for 15 minutes and alert the other nominees.">
                <form className="stack" onSubmit={login.onSubmit}>
                  <FormError message={login.error} />
                  <TextField label="Email" name="email" type="email" required autoComplete="email" />
                  <PasswordField label="Password" name="password" required />
                  <CodeField label="Authenticator code" name="totp" optional required={false} hint="Needed once you've set up two-factor authentication." />
                  <TextField label="Case ID" name="case_id" optional autoComplete="off" hint="Only if you're a nominee on more than one vault." />
                  <Button type="submit" variant="primary" block busy={login.busy}>
                    Sign in
                  </Button>
                </form>
              </Card>
            ),
          },
        ]}
      />
    </>
  );
}

/* -------------------------------------------------------------- journey */
function Journey({ me, refresh }: { me: Me; refresh: () => Promise<void> }) {
  const next = me.nominee.steps.find((s) => !s.done);
  return (
    <div className="stack">
      <PageHeading lede={`Legacy vault of ${me.case.subject_name}`}>Hello, {me.nominee.full_name}</PageHeading>
      <Card title="Your verification" description="Complete each step. You can leave and come back at any time.">
        <Stepper steps={me.nominee.steps} label="Verification steps" />
      </Card>
      {next && <CurrentStep stepKey={next.key} me={me} refresh={refresh} />}
      {me.nominee.verified && <ClaimPanel me={me} refresh={refresh} />}
      {me.nominee.verified && me.released && <VaultPanel />}
      <DevInbox fast={next?.key === "email" || next?.key === "phone"} />
      {me.case.country && <PrivacyNotice country={me.case.country} />}
    </div>
  );
}

function CurrentStep({ stepKey, me, refresh }: { stepKey: string; me: Me; refresh: () => Promise<void> }) {
  switch (stepKey) {
    case "email":
    case "phone":
      return <ContactStep channel={stepKey} refresh={refresh} key={stepKey} />;
    case "mfa":
      return <MfaStep refresh={refresh} />;
    case "identity":
      return <IdentityStep me={me} refresh={refresh} />;
    default:
      return null;
  }
}

function ContactStep({ channel, refresh }: { channel: "email" | "phone"; refresh: () => Promise<void> }) {
  const form = useFormSubmit(async (fd) => {
    await api.post("/otp/verify", { channel, code: str(fd, "code") });
    await refresh();
    return `${channel === "email" ? "Email" : "Phone"} verified.`;
  });
  return (
    <Card
      title={`Confirm your ${channel}`}
      description={`We sent a 6-digit code to the ${channel} the owner registered for you. Codes expire in 10 minutes; 5 wrong tries cancel a code.`}
    >
      <form className="stack" onSubmit={form.onSubmit}>
        <FormError message={form.error} />
        <CodeField label="Verification code" name="code" required />
        <div className="button-row">
          <Button type="submit" variant="primary" busy={form.busy}>
            Verify {channel}
          </Button>
          <ActionButton
            onAction={async () => {
              await api.post("/otp/resend", { channel });
              return "A new code was sent.";
            }}
          >
            Send a new code
          </ActionButton>
        </div>
      </form>
    </Card>
  );
}

function MfaStep({ refresh }: { refresh: () => Promise<void> }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string; instructions: string } | null>(null);
  const confirm = useFormSubmit(async (fd) => {
    await api.post("/mfa/confirm", { code: str(fd, "code") });
    await refresh();
    return "Two-factor authentication is on. You'll need the app every time you sign in.";
  });
  return (
    <Card title="Turn on two-factor authentication" description="Required. It protects the vault even if your password leaks.">
      {!setup ? (
        <ActionButton
          variant="primary"
          onAction={async () => {
            setSetup(await api.post("/mfa/setup"));
          }}
        >
          Set up an authenticator app
        </ActionButton>
      ) : (
        <div className="stack">
          <p>{setup.instructions}</p>
          <div>
            <p className="hint" id="mfa-key-label">
              Setup key — type it into your authenticator app:
            </p>
            <code className="secret" aria-labelledby="mfa-key-label">
              {setup.secret.match(/.{1,4}/g)?.join(" ")}
            </code>
          </div>
          <Disclosure summary="Show the otpauth link instead">
            <code className="secret">{setup.otpauth_uri}</code>
          </Disclosure>
          <form className="stack" onSubmit={confirm.onSubmit}>
            <FormError message={confirm.error} />
            <CodeField label="6-digit code from the app" name="code" required />
            <div className="button-row">
              <Button type="submit" variant="primary" busy={confirm.busy}>
                Turn on two-factor authentication
              </Button>
            </div>
          </form>
        </div>
      )}
    </Card>
  );
}

function IdentityStep({ me, refresh }: { me: Me; refresh: () => Promise<void> }) {
  const status = me.nominee.identity_status;
  const needsRelationship = !me.nominee.identity_hint_registered;
  const form = useFormSubmit(async (fd) => {
    const id = takeFile(fd, "id_file", "ID document");
    const body: Record<string, string> = {
      id_last4: str(fd, "id_last4"),
      date_of_birth: str(fd, "date_of_birth"),
      file_name: id.name,
      content_base64: await fileToBase64(id),
    };
    if (needsRelationship) {
      const rel = takeFile(fd, "relationship_file", "Proof of relationship");
      body.relationship_file_name = rel.name;
      body.relationship_content_base64 = await fileToBase64(rel);
    }
    await api.post("/identity", body);
    await refresh();
    return "Identity submitted for review.";
  });

  if (status === "pending_review") {
    return (
      <Card title="Identity under review">
        <div className="callout" role="status">
          <p>An independent Trove reviewer is checking your documents. This page updates automatically.</p>
        </div>
      </Card>
    );
  }
  return (
    <Card
      title={status === "rejected" ? "Identity rejected — please submit again" : "Prove your identity"}
      description="Your details are compared with what the owner registered (stored only as keyed hashes). Three mismatches suspend the account and alert the owner."
    >
      {status === "rejected" && (
        <div className="callout callout-danger" style={{ marginBottom: "1rem" }}>
          <p>The reviewer couldn't confirm your identity from the last submission. Check the details and upload a clearer document.</p>
        </div>
      )}
      <form className="stack" onSubmit={form.onSubmit}>
        <FormError message={form.error} />
        <div className="form-row">
          <TextField label="Last 4 characters of your ID" name="id_last4" maxLength={4} required={!needsRelationship} optional={needsRelationship} autoComplete="off" hint="Passport or national ID" />
          <TextField label="Your date of birth" name="date_of_birth" type="date" required={!needsRelationship} optional={needsRelationship} autoComplete="bday" />
        </div>
        <FileField label="Photo or scan of your ID" name="id_file" required accept=".pdf,.txt" hint="PDF or TXT, up to 5 MB." />
        {needsRelationship && (
          <FileField
            label="Proof of relationship"
            name="relationship_file"
            required
            accept=".pdf,.txt"
            hint="Birth or marriage certificate, or a legal-heir certificate."
          />
        )}
        <div className="button-row">
          <Button type="submit" variant="primary" busy={form.busy} busyLabel="Uploading securely…">
            Submit identity
          </Button>
        </div>
      </form>
    </Card>
  );
}

/* ---------------------------------------------------------------- claim */
function ClaimPanel({ me, refresh }: { me: Me; refresh: () => Promise<void> }) {
  const active = me.requests.find((r) => ["open", "frozen", "released"].includes(r.status));
  const [totp, setTotp] = useState("");
  const open = useFormSubmit(async (fd) => {
    const cert = takeFile(fd, "certificate", "Death certificate");
    await api.post("/release-requests", {
      date_of_death: str(fd, "date_of_death"),
      file_name: cert.name,
      content_base64: await fileToBase64(cert),
    });
    await refresh();
    return "Claim opened. The other nominees and the owner have been notified.";
  });

  if (!active) {
    return (
      <Card
        title="Report a death and request release"
        description={`Release needs ${me.quorum} verified nominee confirmation(s), reviewer approval of the certificate and a ${me.waiting_period_hours}-hour cooling-off period. The owner is alerted immediately and can cancel if alive.`}
      >
        {me.requests.length > 0 && (
          <ul className="item-list" style={{ marginBottom: "1rem" }}>
            {me.requests.map((r) => (
              <li key={r.id} className="item">
                <div className="item-head">
                  <span className="item-title">Previous request · {formatDate(r.opened_at)}</span>
                  <StatusBadge status={r.status} />
                </div>
                {r.close_reason && <p className="item-meta">{r.close_reason}</p>}
              </li>
            ))}
          </ul>
        )}
        <form className="stack" onSubmit={open.onSubmit}>
          <FormError message={open.error} />
          <TextField label="Date of death" name="date_of_death" type="date" required />
          <FileField label="Death certificate" name="certificate" required accept=".pdf,.txt" hint="PDF or TXT, up to 5 MB." />
          <div className="button-row">
            <Button type="submit" variant="primary" busy={open.busy}>
              Submit death claim
            </Button>
          </div>
        </form>
      </Card>
    );
  }

  const attest = (decision: "confirm" | "dispute") => async () => {
    const send = () => api.post<{ status: string }>(`/release-requests/${enc(active.id)}/attest`, { decision });
    let result: { status: string };
    try {
      result = await send();
    } catch (error) {
      if (!/step-up/i.test(errorMessage(error))) throw error;
      if (!/^\d{6}$/.test(totp.trim())) throw new Error("Enter the 6-digit code from your authenticator app first.");
      await api.post("/step-up", { totp: totp.trim() });
      result = await send();
    }
    setTotp("");
    await refresh();
    return `Recorded: ${decision}. Request is ${humanize(result.status)}.`;
  };

  return (
    <Card title="Release request" actions={<StatusBadge status={active.status} />}>
      <p className="muted">
        Opened {formatDateTime(active.opened_at)} · claimed date of death {formatDate(active.date_of_death)}
      </p>
      <p>
        Confirmations: {active.attestations.length ? active.attestations.map((a) => `${a.full_name} (${a.decision})`).join(", ") : "none yet"}
      </p>
      <GateList gates={active.gates} />
      {active.legal && (
        <div style={{ marginTop: "1rem" }}>
          <LegalRequirements
            legal={active.legal}
            uploadSlot={
              ["open", "frozen"].includes(active.status)
                ? (key, accepted) => <LegalUpload requestId={active.id} requirementKey={key} accepted={accepted} refresh={refresh} />
                : undefined
            }
          />
        </div>
      )}
      {["open", "frozen"].includes(active.status) && !active.you_attested && (
        <section className="stack" aria-labelledby="your-confirmation" style={{ marginTop: "1rem" }}>
          <h3 id="your-confirmation">Your confirmation</h3>
          <p className="muted">Confirm only if you know the death is genuine. Dispute if anything is wrong — that freezes release.</p>
          <div className="field">
            <label htmlFor="attest-totp">Authenticator code</label>
            <p className="hint" id="attest-totp-hint">
              Confirming is a sensitive action, so we re-check your second factor.
            </p>
            <input
              id="attest-totp"
              aria-describedby="attest-totp-hint"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              className="mono"
            />
          </div>
          <div className="button-row">
            <ActionButton variant="primary" onAction={attest("confirm")}>
              Confirm death
            </ActionButton>
            <ActionButton variant="danger" onAction={attest("dispute")}>
              Dispute
            </ActionButton>
          </div>
        </section>
      )}
      {active.status === "open" && (
        <div className="button-row" style={{ marginTop: "1rem" }}>
          <ActionButton
            onAction={async () => {
              const r = await api.post<{ status: string }>(`/release-requests/${enc(active.id)}/evaluate`);
              await refresh();
              return `Request is ${humanize(r.status)}.`;
            }}
          >
            Re-check gates
          </ActionButton>
        </div>
      )}
    </Card>
  );
}

function LegalUpload({
  requestId,
  requirementKey,
  accepted,
  refresh,
}: {
  requestId: string;
  requirementKey: string;
  accepted: string[];
  refresh: () => Promise<void>;
}) {
  const form = useFormSubmit(async (fd) => {
    const file = takeFile(fd, "file", "Legal document");
    await api.post(`/release-requests/${enc(requestId)}/legal-documents`, {
      requirement_key: requirementKey,
      document_type: str(fd, "document_type"),
      file_name: file.name,
      content_base64: await fileToBase64(file),
    });
    await refresh();
    return "Legal document uploaded for review.";
  });
  return (
    <form className="stack-sm legal-upload" onSubmit={form.onSubmit}>
      <FormError message={form.error} />
      <SelectField label="Document you have" name="document_type" options={accepted.map((a) => ({ value: a, label: a }))} />
      <FileField label="Upload it" name="file" required accept=".pdf,.txt" hint="PDF or TXT, up to 5 MB." />
      <div className="button-row">
        <Button type="submit" busy={form.busy}>
          Upload for review
        </Button>
      </div>
    </form>
  );
}

function PrivacyNotice({ country }: { country: string }) {
  const profile = useResource((s) => publicApi.get<CountryProfile>(`/jurisdictions/${enc(country)}`, s), [country]);
  if (!profile.data) return null;
  const p = profile.data.privacy;
  return (
    <Card title="Your privacy" description={`${p.law} · ${profile.data.name}`}>
      <p>{p.notice}</p>
      <Disclosure summary="Your rights">
        <ul>
          {p.rights.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p className="hint">Regulator: {p.regulator}</p>
      </Disclosure>
    </Card>
  );
}

/* ---------------------------------------------------------------- vault */
function VaultPanel() {
  const vault = useResource((s) => api.get<VaultView>("/vault", s), []);
  const [unlockedUntil, setUnlockedUntil] = useState<number | null>(null);
  const unlock = useFormSubmit(async (fd) => {
    await api.post("/step-up", { totp: str(fd, "totp") });
    setUnlockedUntil(Date.now() + 10 * 60_000);
    return "Downloads unlocked for 10 minutes.";
  });
  const unlocked = unlockedUntil !== null && unlockedUntil > Date.now();
  const v = vault.data;
  return (
    <Card title="Your released legacy vault" description={v?.full_access ? "As executor you can see every document." : "You can see only the document types the owner shared with you."}>
      {!v ? (
        <Skeleton />
      ) : (
        <div className="stack">
          <form className="stack" onSubmit={unlock.onSubmit}>
            <FormError message={unlock.error} />
            {unlocked ? (
              <div className="callout callout-success" role="status">
                <p>Downloads are unlocked until {new Date(unlockedUntil!).toLocaleTimeString()}.</p>
              </div>
            ) : (
              <>
                <CodeField label="Authenticator code to unlock downloads" name="totp" required hint="Valid for 10 minutes." />
                <div className="button-row">
                  <Button type="submit" busy={unlock.busy}>
                    Unlock downloads
                  </Button>
                </div>
              </>
            )}
          </form>
          <section aria-labelledby="vault-docs">
            <h3 id="vault-docs">Documents</h3>
            {v.documents.length === 0 ? (
              <p className="muted">No documents were shared with you.</p>
            ) : (
              <ul className="item-list">
                {v.documents.map((d) => (
                  <li key={d.id} className="item">
                    <div className="item-head">
                      <span className="item-title">{d.file_name}</span>
                      <Badge tone="neutral">{humanize(d.category)}</Badge>
                    </div>
                    <div className="item-actions">
                      <a className="btn btn-secondary btn-sm" href={`/api/nominee/vault/documents/${enc(d.id)}`}>
                        Download<span className="sr-only"> {d.file_name}</span>
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="vault-assets">
            <h3 id="vault-assets">Assets on record</h3>
            {v.assets.length === 0 ? (
              <p className="muted">None recorded.</p>
            ) : (
              <ul className="item-list">
                {v.assets.map((a) => (
                  <li key={a.id} className="item">
                    <div className="item-head">
                      <span className="item-title">{humanize(a.asset_type)}</span>
                      <StatusBadge status={a.status} />
                    </div>
                    <p className="item-meta">{[a.institution ?? "Institution unknown", a.masked_reference].filter(Boolean).join(" · ")}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="vault-shares">
            <h3 id="vault-shares">Beneficiary shares</h3>
            {v.beneficiaries.length === 0 ? (
              <p className="muted">None recorded.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Relationship</th>
                      <th scope="col">Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.beneficiaries.map((b) => (
                      <tr key={b.id}>
                        <th scope="row">{b.name}</th>
                        <td>{humanize(b.relationship)}</td>
                        <td>{b.share_percent != null ? `${b.share_percent}%` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------- dev inbox */
function DevInbox({ fast }: { fast: boolean }) {
  const outbox = useResource((s) => api.get<{ messages: OutboxMessage[] }>("/outbox", s), []);
  usePolling(() => void outbox.reload(), fast ? 5_000 : 30_000);
  if (outbox.error || !outbox.data) return null;
  return (
    <Card
      title={
        <>
          Your phone &amp; email inbox <Badge tone="neutral">development only</Badge>
        </>
      }
      description="No SMS/email provider is configured, so messages to your registered phone and email appear here. In production they are delivered for real and never shown on screen."
    >
      {outbox.data.messages.length === 0 ? (
        <EmptyState title="No messages" />
      ) : (
        <ul className="item-list" aria-live="polite">
          {outbox.data.messages.map((m, i) => (
            <li key={i} className="item">
              <div className="item-head">
                <span className="item-title">
                  {humanize(m.channel)} · {m.destination_masked}
                </span>
                <time className="muted" dateTime={m.created_at}>
                  {formatDateTime(m.created_at)}
                </time>
              </div>
              <p className="item-meta mono">{m.body}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
