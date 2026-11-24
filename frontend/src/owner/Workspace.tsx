import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { errorMessage } from "../shared/api";
import { Button } from "../shared/components/Button";
import { Dialog } from "../shared/components/Dialog";
import { EmptyState, ErrorNote, Skeleton } from "../shared/components/Display";
import { FormError, SelectField, TextField } from "../shared/components/Field";
import { AppHeader, Footer, PageHeading } from "../shared/components/Layout";
import { useToast } from "../shared/components/Toaster";
import { useDocumentTitle, useHashRoute, useResource } from "../shared/hooks";
import type { CaseItem, CountryCode, JurisdictionCatalog, Mode, Workflow } from "../shared/types";
import { api, casePath } from "./api";
import { CaseContext, SECTIONS, type SectionKey } from "./CaseContext";
import { AssistantSection } from "./sections/AssistantSection";
import { ClaimsSection } from "./sections/ClaimsSection";
import { ComplianceSection } from "./sections/ComplianceSection";
import { DiscoverySection } from "./sections/DiscoverySection";
import { DocumentsSection } from "./sections/DocumentsSection";
import { GraphSection } from "./sections/GraphSection";
import { OverviewSection } from "./sections/OverviewSection";
import { PeopleSection } from "./sections/PeopleSection";
import { PlanSection } from "./sections/PlanSection";

const NAV: { key: SectionKey; label: string; icon: string; group?: string }[] = [
  { key: "overview", label: "Overview", icon: "◎" },
  { key: "plan", label: "Plan & nominees", icon: "🛡", group: "Modes" },
  { key: "discovery", label: "Discovery", icon: "🔍" },
  { key: "documents", label: "Documents", icon: "📄", group: "Case records" },
  { key: "people", label: "People", icon: "👥" },
  { key: "claims", label: "Claims", icon: "✔" },
  { key: "graph", label: "Knowledge graph", icon: "⌘", group: "Insight" },
  { key: "compliance", label: "Country & compliance", icon: "⚖" },
  { key: "assistant", label: "Assistant", icon: "💬" },
];

const TITLES: Record<SectionKey, { title: string; lede: string }> = {
  overview: { title: "Case overview", lede: "Where this case stands and what to do next." },
  plan: {
    title: "Plan · Legacy vault",
    lede: "Decide who may receive the vault and on what terms. Nothing is released until every security gate passes.",
  },
  discovery: {
    title: "Discovery · Start from anything",
    lede: "No access to accounts? Start with a phone, an email address or one statement. Each source creates leads; leads seen in more than one source gain confidence.",
  },
  documents: { title: "Documents", lede: "Encrypted vault documents and death-certificate review." },
  people: { title: "People & beneficiaries", lede: "Beneficiary records, possible duplicates and relationships." },
  claims: { title: "Claims tracker", lede: "Follow up with each institution. Trove never submits claims for you." },
  graph: { title: "Knowledge graph", lede: "People, contacts, institutions, assets and evidence, linked to surface hidden relationships." },
  compliance: {
    title: "Country & compliance",
    lede: "What changes in this case's country: legal documents, deadlines, privacy law and where data is stored.",
  },
  assistant: { title: "Assistant", lede: "Ask about this case. Answers are local unless you choose the LLM for a question." },
};

export function Workspace({ user, onSignedOut }: { user: { email?: string | null } | null; onSignedOut: () => void }) {
  const toast = useToast();
  const [section, go] = useHashRoute(SECTIONS, "overview");
  const [caseId, setCaseId] = useState<string>("");
  const [version, setVersion] = useState(0);
  const [creating, setCreating] = useState(false);
  const changed = useCallback(() => setVersion((v) => v + 1), []);

  const cases = useResource((signal) => api.get<{ cases: CaseItem[] }>("/cases", signal), [version]);
  const list = cases.data?.cases ?? [];
  const caseItem = list.find((c) => c.id === caseId);

  // pick a case: remembered > first
  useEffect(() => {
    if (!cases.data) return;
    if (caseItem) return;
    let remembered = "";
    try {
      remembered = window.sessionStorage.getItem("trove-case") ?? "";
    } catch {
      /* ignore */
    }
    const next = list.find((c) => c.id === remembered) ?? list[0];
    setCaseId(next?.id ?? "");
  }, [cases.data, caseItem, list]);

  useEffect(() => {
    try {
      if (caseId) window.sessionStorage.setItem("trove-case", caseId);
    } catch {
      /* ignore */
    }
  }, [caseId]);

  const workflow = useResource(
    (signal) => api.get<Workflow>(casePath(caseId, "/workflow"), signal),
    [caseId, version],
    Boolean(caseId),
  );

  const counts: Partial<Record<SectionKey, number>> = {
    overview: workflow.data?.assets_needing_review || undefined,
    people: workflow.data?.unverified_beneficiaries || undefined,
  };

  const meta = TITLES[section];
  useDocumentTitle(`${meta.title}${caseItem ? ` · ${caseItem.subject_name}` : ""} · Trove`);

  async function signOut() {
    try {
      await api.post("/auth/logout");
    } catch {
      /* already signed out */
    }
    try {
      window.sessionStorage.removeItem("trove-case");
    } catch {
      /* ignore */
    }
    toast.info("Signed out.");
    onSignedOut();
  }

  const context = useMemo(
    () => (caseItem ? { caseId, caseItem, version, changed, go } : null),
    [caseId, caseItem, version, changed, go],
  );

  return (
    <>
      <AppHeader area="Owner workspace" home="/">
        {list.length > 0 && (
          <label className="case-switcher">
            <span className="sr-only">Current case</span>
            <select value={caseId} onChange={(event) => setCaseId(event.target.value)}>
              {list.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.subject_name} · {item.mode === "plan" ? "Plan" : "Discovery"}
                  {item.country ? ` · ${item.country === "OTHER" ? "Other" : item.country}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
          + New case
        </Button>
        {user?.email && <span className="user-chip">{user.email}</span>}
        <Button size="sm" onClick={signOut}>
          Sign out
        </Button>
      </AppHeader>

      <div className="shell">
        <nav className="side-nav" aria-label="Case sections">
          <ul>
            {NAV.map((item) => (
              <li key={item.key}>
                {item.group && (
                  <p className="nav-group" aria-hidden="true">
                    {item.group}
                  </p>
                )}
                <a
                  href={`#/${item.key}`}
                  aria-current={section === item.key ? "page" : undefined}
                  onClick={(event) => {
                    event.preventDefault();
                    go(item.key);
                  }}
                >
                  <span className="nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="nav-label">{item.label}</span>
                  {counts[item.key] ? (
                    <span className="nav-count">
                      {counts[item.key]}
                      <span className="sr-only"> need attention</span>
                    </span>
                  ) : null}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <main id="main" className="main" tabIndex={-1}>
          <PageHeading focusKey={section} lede={meta.lede}>
            {meta.title}
          </PageHeading>

          {cases.error ? (
            <ErrorNote message={cases.error} onRetry={cases.reload} />
          ) : !cases.data ? (
            <Skeleton lines={5} label="Loading cases" />
          ) : !context ? (
            <EmptyState
              title="No cases yet"
              action={
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Start your first case
                </Button>
              }
            >
              <p>
                Choose <strong>Plan</strong> to prepare your own legacy vault, or <strong>Discovery</strong> to trace a loved one's
                assets.
              </p>
            </EmptyState>
          ) : (
            <CaseContext.Provider value={context}>
              <SectionView key={caseId} section={section} />
            </CaseContext.Provider>
          )}
        </main>
      </div>
      <Footer />

      <NewCaseDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={async (item) => {
          setCreating(false);
          await cases.reload();
          setCaseId(item.id);
          go(item.mode === "plan" ? "plan" : "discovery");
          toast.success(`Case for ${item.subject_name} created.`);
        }}
      />
    </>
  );
}

function SectionView({ section }: { section: SectionKey }) {
  switch (section) {
    case "overview":
      return <OverviewSection />;
    case "plan":
      return <PlanSection />;
    case "discovery":
      return <DiscoverySection />;
    case "documents":
      return <DocumentsSection />;
    case "people":
      return <PeopleSection />;
    case "claims":
      return <ClaimsSection />;
    case "graph":
      return <GraphSection />;
    case "compliance":
      return <ComplianceSection />;
    case "assistant":
      return <AssistantSection />;
  }
}

function NewCaseDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (item: CaseItem) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [country, setCountry] = useState<CountryCode>("IN");
  const catalog = useResource((s) => api.get<JurisdictionCatalog>("/jurisdictions", s), [], open);
  const selected = catalog.data?.countries.find((c) => c.code === country);
  const region = catalog.data?.data_region;
  const mismatch = Boolean(selected && region && selected.data_region !== "any" && selected.data_region !== region);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const jurisdiction = String(form.get("jurisdiction") ?? "").trim();
      const item = await api.post<CaseItem>("/cases", {
        subject_name: String(form.get("subject_name")),
        country,
        ...(jurisdiction ? { jurisdiction } : {}),
        mode: String(form.get("mode")) as Mode,
      });
      onCreated(item);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Start a case">
      <form className="stack" onSubmit={submit}>
        <FormError message={error} />
        <fieldset className="checks">
          <legend>What do you want to do?</legend>
          <label className="check">
            <input type="radio" name="mode" value="plan" defaultChecked />
            <span>
              <strong>Plan</strong> — I'm preparing my own legacy vault and choosing nominees.
            </span>
          </label>
          <label className="check">
            <input type="radio" name="mode" value="discovery" />
            <span>
              <strong>Discovery</strong> — someone has died and I need to find what they held.
            </span>
          </label>
        </fieldset>
        <TextField label="Whose estate is this?" name="subject_name" required maxLength={200} autoComplete="off" hint="Full name" />
        <SelectField
          label="Country whose law applies"
          name="country"
          value={country}
          onChange={(e) => setCountry(e.target.value as CountryCode)}
          hint="Usually where the person lived. This sets the legal documents, deadlines and privacy rules."
          options={(catalog.data?.countries ?? [{ code: "IN", name: "India" } as { code: CountryCode; name: string }]).map((c) => ({
            value: c.code,
            label: c.name,
          }))}
        />
        <TextField
          label={selected?.states_label ?? "State / region"}
          name="jurisdiction"
          optional
          maxLength={100}
          autoComplete="address-level1"
          hint={selected?.examples.length ? `e.g. ${selected.examples.join(", ")}` : undefined}
          key={country}
        />
        {mismatch && (
          <div className={`callout ${catalog.data?.enforce_residency ? "callout-danger" : "callout-warning"}`} role="status">
            <p>
              This deployment stores data in region "{region}", but {selected?.name} cases are best hosted in "{selected?.data_region}".
              {catalog.data?.enforce_residency ? " Residency is enforced, so this case will be refused." : ""}
            </p>
          </div>
        )}
        <div className="button-row end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={busy}>
            Create case
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
