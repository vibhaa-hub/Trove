import { useMemo, useState } from "react";
import { takeFile, enc, fileToBase64 } from "../../shared/api";
import { ActionButton } from "../../shared/components/ActionButton";
import { Button } from "../../shared/components/Button";
import {
  Badge,
  Card,
  ConfidenceMeter,
  Disclosure,
  EmptyState,
  ErrorNote,
  Skeleton,
  StatusBadge,
} from "../../shared/components/Display";
import { CheckboxGroup, FileField, FormError, PasswordField, SelectField, TextField } from "../../shared/components/Field";
import { Tabs } from "../../shared/components/Tabs";
import { useToast } from "../../shared/components/Toaster";
import { str, useFormSubmit } from "../../shared/components/useFormSubmit";
import { humanize, plural, splitList } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { Connector, DiscoveryOverview, Lead, MockEmail } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

type TabKey = "start" | "sources" | "leads" | "coverage";

const HAVE = [
  { value: "death_certificate", label: "Death certificate" },
  { value: "name_dob", label: "Name and date of birth" },
  { value: "national_id", label: "National ID / PAN / SSN" },
  { value: "email_address", label: "Their email address" },
  { value: "email_access", label: "Access to their email" },
  { value: "phone_number", label: "Their phone number" },
  { value: "phone_access", label: "Their phone (unlocked)" },
  { value: "cloud_access", label: "Access to their cloud drive" },
  { value: "documents", label: "One or two documents" },
];

const PRIORITY_TONE: Record<string, "danger" | "warning" | "info"> = { high: "danger", medium: "warning", low: "info" };

export function DiscoverySection() {
  const { caseId, version, changed } = useCase();
  const [tab, setTab] = useState<TabKey>("start");
  const overview = useResource((s) => api.get<DiscoveryOverview>(casePath(caseId, "/discovery"), s), [caseId, version]);

  /** Most discovery endpoints return the fresh overview; show it immediately and refresh the rest. */
  const apply = (next: DiscoveryOverview) => {
    overview.setData(next);
    changed();
  };

  if (overview.error) return <ErrorNote message={overview.error} onRetry={overview.reload} />;
  const data = overview.data;
  if (!data) return <Skeleton lines={6} label="Loading discovery" />;
  const newLeads = data.leads.filter((l) => l.status === "new").length;
  const gaps = data.coverage.filter((c) => c.status === "gap").length;

  return (
    <Tabs<TabKey>
      label="Discovery steps"
      active={tab}
      onChange={setTab}
      items={[
        { key: "start", label: "1 · What you have", panel: <StartTab data={data} apply={apply} /> },
        { key: "sources", label: "2 · Add sources", panel: <SourcesTab data={data} apply={apply} goLeads={() => setTab("leads")} /> },
        { key: "leads", label: "3 · Leads", badge: newLeads || undefined, panel: <LeadsTab leads={data.leads} apply={apply} /> },
        { key: "coverage", label: "4 · What's missing", badge: gaps || undefined, panel: <CoverageTab data={data} /> },
      ]}
    />
  );
}

/* --------------------------------------------------------------- start */
function StartTab({ data, apply }: { data: DiscoveryOverview; apply: (d: DiscoveryOverview) => void }) {
  const { caseId } = useCase();
  const form = useFormSubmit(
    async (fd) => {
      const next = await api.post<DiscoveryOverview>(casePath(caseId, "/discovery/seeds"), {
        have: fd.getAll("have").map(String),
        emails: splitList(str(fd, "emails")),
        phones: splitList(str(fd, "phones")),
        employers: splitList(str(fd, "employers")),
      });
      apply(next);
      return "Starting plan updated.";
    },
    { reset: false },
  );
  return (
    <div className="grid grid-2">
      <Card title="Tell us what you have" description="Anything helps — even just a phone number.">
        <form className="stack" onSubmit={form.onSubmit} key={data.have.join()}>
          <FormError message={form.error} />
          <CheckboxGroup legend="I have…" name="have" options={HAVE} defaultChecked={data.have} columns={2} />
          <TextField label="Their email addresses" name="emails" optional hint="Separate with commas" autoComplete="off" />
          <TextField label="Their phone numbers" name="phones" optional hint="Separate with commas" autoComplete="off" />
          <TextField label="Employers, current or past" name="employers" optional hint="Separate with commas" autoComplete="off" />
          <p className="hint">Contacts are stored as keyed hashes for matching, never in plain text.</p>
          <div className="button-row">
            <Button type="submit" variant="primary" busy={form.busy}>
              Build my starting plan
            </Button>
          </div>
        </form>
      </Card>
      <Card title="Your next steps" description="Updated as you add information and sources.">
        {data.plan.length === 0 ? (
          <EmptyState title="Tell us what you have to get a plan" />
        ) : (
          <ol className="plan-list">
            {data.plan.map((step, i) => (
              <li key={i}>
                <div className="item-head">
                  <strong>{step.title}</strong>
                  <Badge tone={PRIORITY_TONE[step.priority] ?? "info"}>{humanize(step.priority)} priority</Badge>
                </div>
                <p className="item-meta">{step.why}</p>
                <p className="action">{step.action}</p>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- sources */
function SourcesTab({
  data,
  apply,
  goLeads,
}: {
  data: DiscoveryOverview;
  apply: (d: DiscoveryOverview) => void;
  goLeads: () => void;
}) {
  const { caseId } = useCase();
  const [type, setType] = useState("sms_export");
  const catalog = useResource((s) => api.get<{ connectors: Connector[] }>("/connectors", s), []);

  const upload = useFormSubmit(async (fd) => {
    const file = takeFile(fd, "file", "File");
    const body: Record<string, string> = {
      source_type: str(fd, "source_type"),
      file_name: file.name,
      content_base64: await fileToBase64(file),
    };
    if (body.source_type === "document") body.category = str(fd, "category");
    const next = await api.post<DiscoveryOverview>(casePath(caseId, "/discovery/sources"), body);
    apply(next);
    setType("sms_export");
    return `Scanned ${plural(next.scan?.items_scanned ?? 0, "item")}, found ${plural(next.scan?.signals_found ?? 0, "signal")}.`;
  });

  return (
    <div className="stack">
      <div className="grid grid-2">
        <Card title="Upload an export or document" description={data.privacy}>
          <form className="stack" onSubmit={upload.onSubmit}>
            <FormError message={upload.error} />
            <SelectField
              label="What is it?"
              name="source_type"
              value={type}
              onChange={(e) => setType(e.target.value)}
              options={[
                { value: "sms_export", label: "Phone SMS export (.xml / .csv / .txt)" },
                { value: "email_export", label: "Email export (.mbox / .eml)" },
                { value: "cloud_export", label: "Cloud export (.zip) or file listing (.txt)" },
                { value: "document", label: "Document (statement, policy, tax return…)" },
              ]}
            />
            {type === "document" && (
              <SelectField
                label="Document type"
                name="category"
                options={[
                  { value: "bank_statement", label: "Bank statement" },
                  { value: "insurance_policy", label: "Insurance policy" },
                  { value: "investment_statement", label: "Investment statement" },
                  { value: "tax_return", label: "Tax return" },
                  { value: "pension_record", label: "Pension record" },
                  { value: "property_document", label: "Property document" },
                  { value: "other", label: "Other" },
                ]}
              />
            )}
            <FileField label="File" name="file" required accept=".xml,.csv,.txt,.mbox,.eml,.zip,.json,.pdf" hint="Max 5 MB. Scanned on the server; nothing is sent to third parties." />
            <div className="button-row">
              <Button type="submit" variant="primary" busy={upload.busy} busyLabel="Scanning…">
                Scan source
              </Button>
            </div>
          </form>
        </Card>
        <Card title="Sources scanned" actions={<Button size="sm" onClick={goLeads}>View leads</Button>}>
          {data.sources.length === 0 ? (
            <EmptyState title="No sources yet">Upload an export, connect a mailbox, or try the demo inbox.</EmptyState>
          ) : (
            <ul className="item-list">
              {data.sources.map((s, i) => (
                <li key={i} className="item">
                  <div className="item-head">
                    <span className="item-title">{s.label}</span>
                    <Badge tone="neutral">{humanize(s.source_type)}</Badge>
                  </div>
                  <p className="item-meta">
                    {plural(s.items_scanned, "item")} scanned · {plural(s.signals_found, "signal")} found
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <ImapCard apply={apply} />
        <FolderCard apply={apply} />
      </div>
      <MockInboxCard />
      {catalog.data && (
        <Card title="All connectors" level={2} description="Planned connectors need OAuth app registration before they can be enabled.">
          <ul className="item-list">
            {catalog.data.connectors.map((c) => (
              <li key={c.id} className="item">
                <div className="item-head">
                  <span className="item-title">{c.name}</span>
                  {c.status === "available" ? (
                    <Badge tone="success">Available</Badge>
                  ) : c.status === "planned" ? (
                    <Badge tone="info">Planned</Badge>
                  ) : (
                    <Badge tone="warning">{humanize(c.status)}</Badge>
                  )}
                </div>
                {c.needs && <p className="item-meta">Needs: {c.needs.join(", ")}</p>}
                {c.stores_credentials === false && <p className="item-meta">Credentials are never stored.</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function ImapCard({ apply }: { apply: (d: DiscoveryOverview) => void }) {
  const { caseId } = useCase();
  const form = useFormSubmit(async (fd) => {
    const body: Record<string, unknown> = {
      username: str(fd, "username"),
      app_password: String(fd.get("app_password") ?? ""),
      since_days: Number(fd.get("since_days")) || 730,
    };
    if (str(fd, "host")) body.host = str(fd, "host");
    const next = await api.post<DiscoveryOverview>(casePath(caseId, "/discovery/connectors/imap"), body);
    apply(next);
    return `Mailbox scanned: ${plural(next.scan?.items_scanned ?? 0, "message")}, ${plural(next.scan?.signals_found ?? 0, "signal")}.`;
  });
  return (
    <Card
      title="Connect a mailbox (IMAP)"
      description="Read-only. Only finance-looking messages are opened. The app password is used once and never stored."
    >
      <form className="stack" onSubmit={form.onSubmit} autoComplete="off">
        <FormError message={form.error} />
        <TextField label="Mailbox address" name="username" type="email" required autoComplete="off" />
        <PasswordField label="App password" name="app_password" required autoComplete="off" hint="Create one in the mailbox's security settings (Gmail, Outlook, Yahoo, iCloud, Zoho)." />
        <div className="form-row">
          <TextField label="IMAP host" name="host" optional placeholder="imap.example.com" hint="Not needed for well-known providers." autoComplete="off" />
          <TextField label="Look back (days)" name="since_days" type="number" min={1} max={3650} defaultValue={730} />
        </div>
        <div className="button-row">
          <Button type="submit" variant="primary" busy={form.busy} busyLabel="Scanning mailbox… this can take a minute">
            Scan mailbox
          </Button>
        </div>
      </form>
    </Card>
  );
}

function FolderCard({ apply }: { apply: (d: DiscoveryOverview) => void }) {
  const { caseId } = useCase();
  const form = useFormSubmit(
    async (fd) => {
      const next = await api.post<DiscoveryOverview>(casePath(caseId, "/discovery/connectors/folder"), {
        path: str(fd, "path") || ".",
      });
      apply(next);
      return `Folder scanned: ${plural(next.scan?.items_scanned ?? 0, "file")}, ${plural(next.scan?.signals_found ?? 0, "signal")}.`;
    },
    { reset: false },
  );
  return (
    <Card title="Scan a device folder" description="A USB drive, NAS or laptop folder mounted under the server's import root.">
      <form className="stack" onSubmit={form.onSubmit}>
        <FormError message={form.error} />
        <TextField label="Folder path" name="path" defaultValue="." hint="Relative to the import root. Paths outside it are refused." autoComplete="off" />
        <div className="button-row">
          <Button type="submit" busy={form.busy} busyLabel="Scanning folder…">
            Scan folder
          </Button>
        </div>
      </form>
    </Card>
  );
}

function MockInboxCard() {
  const { caseId, changed } = useCase();
  const toast = useToast();
  const [messages, setMessages] = useState<MockEmail[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [email, setEmail] = useState("");

  const load = useFormSubmit(
    async () => {
      const result = await api.post<{ email: string; messages: MockEmail[] }>(
        casePath(caseId, "/integrations/mock-email/connect"),
        { email, max_messages: 25 },
      );
      setMessages(result.messages);
      setSelected(new Set());
      return `Loaded ${plural(result.messages.length, "fictional email")} for ${result.email}.`;
    },
    { reset: false },
  );

  async function analyse() {
    const result = await api.post<{
      messages_analyzed: number;
      duplicates_skipped: number;
      results: { subject: string; assets_created: unknown[] }[];
    }>(casePath(caseId, "/integrations/mock-email/sync"), { email, message_ids: [...selected] });
    const leads = result.results.reduce((n, r) => n + r.assets_created.length, 0);
    changed();
    if (!leads) toast.info("No new leads in the selected messages.");
    return `Analysed ${plural(result.messages_analyzed, "email")} (${result.duplicates_skipped} duplicate) and found ${plural(leads, "lead")}. Review them in Overview.`;
  }

  return (
    <Card title="Demo inbox (fictional)" description="Try Discovery safely: generated sample emails, no real account and no external connection.">
      <form className="stack" onSubmit={load.onSubmit}>
        <FormError message={load.error} />
        <div className="form-row">
          <TextField
            label="Demo email address"
            name="email"
            type="email"
            required
            placeholder="you@example.test"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="button-row">
          <Button type="submit" busy={load.busy}>
            Load sample inbox
          </Button>
        </div>
      </form>
      {messages.length > 0 && (
        <fieldset className="checks" style={{ marginTop: "1rem" }}>
          <legend>Choose messages to analyse</legend>
          <div className="button-row" style={{ marginBottom: "0.5rem" }}>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set(messages.map((m) => m.id)))}>
              Select all
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </div>
          <div className="checks-list">
            {messages.map((m) => (
              <label key={m.id} className="check">
                <input
                  type="checkbox"
                  checked={selected.has(m.id)}
                  onChange={(e) => {
                    const next = new Set(selected);
                    if (e.target.checked) next.add(m.id);
                    else next.delete(m.id);
                    setSelected(next);
                  }}
                />
                <span>
                  <strong>{m.subject}</strong> <span className="muted">· {m.from} · {m.date}</span>
                </span>
              </label>
            ))}
          </div>
          <div className="button-row" style={{ marginTop: "0.75rem" }}>
            <ActionButton variant="primary" disabled={!selected.size} onAction={analyse}>
              Analyse {selected.size ? plural(selected.size, "message") : "selected"}
            </ActionButton>
          </div>
        </fieldset>
      )}
    </Card>
  );
}

/* --------------------------------------------------------------- leads */
type SortKey = "confidence" | "sources" | "category";

function LeadsTab({ leads, apply }: { leads: Lead[]; apply: (d: DiscoveryOverview) => void }) {
  const { caseId } = useCase();
  const [status, setStatus] = useState("active");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "confidence", desc: true });

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = leads.filter((l) => {
      if (status === "active" && l.status === "dismissed") return false;
      if (status !== "active" && status !== "all" && l.status !== status) return false;
      if (!q) return true;
      return [l.category_label, l.institution ?? "", ...l.masked_refs].join(" ").toLowerCase().includes(q);
    });
    const value = (l: Lead) => (sort.key === "confidence" ? l.confidence : sort.key === "sources" ? l.source_count : l.category_label);
    return filtered.sort((a, b) => {
      const [x, y] = [value(a), value(b)];
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.desc ? -cmp : cmp;
    });
  }, [leads, status, query, sort]);

  function header(key: SortKey, label: string) {
    const active = sort.key === key;
    return (
      <th scope="col" aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}>
        <button
          type="button"
          className="sort-button"
          onClick={() => setSort({ key, desc: active ? !sort.desc : key !== "category" })}
        >
          {label}
          <span aria-hidden="true">{active ? (sort.desc ? " ▼" : " ▲") : " ↕"}</span>
        </button>
      </th>
    );
  }

  const decide = (lead: Lead, decision: "promote" | "dismiss" | "reopen") => async () => {
    const next = await api.patch<DiscoveryOverview>(casePath(caseId, `/discovery/leads/${enc(lead.id)}`), { decision });
    apply(next);
    return decision === "promote" ? "Lead added to the asset register for review." : decision === "dismiss" ? "Lead dismissed." : "Lead reopened.";
  };

  if (!leads.length) return <EmptyState title="No leads yet">Add a source in step 2 to find leads.</EmptyState>;

  return (
    <Card title="Leads" description="Promote real ones into the asset register. Leads seen in several sources are more reliable.">
      <div className="toolbar" role="search" aria-label="Filter leads">
        <SelectField
          label="Show"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          options={[
            { value: "active", label: "New and promoted" },
            { value: "new", label: "New only" },
            { value: "promoted", label: "Promoted" },
            { value: "dismissed", label: "Dismissed" },
            { value: "all", label: "All" },
          ]}
        />
        <TextField label="Search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Institution or category" />
      </div>
      <p className="muted" aria-live="polite">
        Showing {plural(rows.length, "lead")} of {leads.length}.
      </p>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Discovered leads. Column headers with buttons can be sorted.</caption>
          <thead>
            <tr>
              {header("category", "Category")}
              <th scope="col">Institution</th>
              <th scope="col">References</th>
              {header("sources", "Sources")}
              {header("confidence", "Confidence")}
              <th scope="col">Next step</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((lead) => {
              const name = `${lead.category_label}${lead.institution ? ` at ${lead.institution}` : ""}`;
              return (
                <tr key={lead.id} className={lead.status === "dismissed" ? "dim" : ""}>
                  <th scope="row">{lead.category_label}</th>
                  <td>{lead.institution || <span className="muted">Unknown — corroborate</span>}</td>
                  <td>{lead.masked_refs.length ? lead.masked_refs.join(", ") : "—"}</td>
                  <td>
                    {lead.source_count}
                    <span className="muted"> ({lead.source_types.map(humanize).join(", ")})</span>
                    {lead.evidence.length > 0 && (
                      <Disclosure summary="Evidence">
                        <ul>
                          {lead.evidence.map((e, i) => (
                            <li key={i}>{e}</li>
                          ))}
                        </ul>
                      </Disclosure>
                    )}
                  </td>
                  <td>
                    <ConfidenceMeter value={lead.confidence} />
                  </td>
                  <td>{lead.next_step}</td>
                  <td>
                    <div className="button-row">
                      {lead.status === "new" && (
                        <>
                          <ActionButton size="sm" variant="primary" onAction={decide(lead, "promote")} aria-label={`Promote ${name}`}>
                            Promote
                          </ActionButton>
                          <ActionButton size="sm" onAction={decide(lead, "dismiss")} aria-label={`Dismiss ${name}`}>
                            Dismiss
                          </ActionButton>
                        </>
                      )}
                      {lead.status === "dismissed" && (
                        <ActionButton size="sm" onAction={decide(lead, "reopen")} aria-label={`Reopen ${name}`}>
                          Reopen
                        </ActionButton>
                      )}
                      {lead.status !== "new" && lead.status !== "dismissed" && <StatusBadge status="promoted" />}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------ coverage */
function CoverageTab({ data }: { data: DiscoveryOverview }) {
  return (
    <div className="stack">
      <Card title="Coverage map" description="Asset types found so far, and where to look for the gaps.">
        <ul className="coverage" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {data.coverage.map((c) => (
            <li key={c.label} className={`coverage-item ${c.status}`}>
              <div className="item-head">
                <strong>{c.label}</strong>
                {c.status === "found" ? <Badge tone="success">{plural(c.leads, "lead")}</Badge> : <Badge tone="warning">Gap</Badge>}
              </div>
              {c.status === "gap" && <p className="item-meta">{c.where_to_look}</p>}
              {c.official_tracing.length > 0 && (
                <ul>
                  {c.official_tracing.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="No access to email, phone or cloud?" description="Official recovery routes for a deceased person's accounts.">
        {data.access_recovery.map((g) => (
          <Disclosure key={g.title} summary={g.title}>
            <ul>
              {g.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </Disclosure>
        ))}
      </Card>
    </div>
  );
}
