import { ActionButton } from "../../shared/components/ActionButton";
import { Badge, Card, ErrorNote, Skeleton } from "../../shared/components/Display";
import { formatDate, humanize, plural } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { Compliance } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

export function ComplianceSection() {
  const { caseId, version, changed, go, caseItem } = useCase();
  const data = useResource((s) => api.get<Compliance>(casePath(caseId, "/compliance"), s), [caseId, version]);

  if (data.error) return <ErrorNote message={data.error} onRetry={data.reload} />;
  const c = data.data;
  if (!c) return <Skeleton lines={8} label="Loading country rules" />;
  const p = c.profile;
  const residency = c.data_residency;

  return (
    <div className="stack">
      <div className="callout">
        <p>
          <strong>
            {p.name}
            {c.jurisdiction && c.jurisdiction !== p.name ? ` · ${c.jurisdiction}` : ""}
          </strong>{" "}
          — {p.disclaimer} <span className="muted">(Rules last reviewed {p.last_reviewed}.)</span>
        </p>
      </div>

      <div className="grid grid-2">
        <Card title="How entitlement works" description="Who can receive what, and through which legal process.">
          <p>{p.succession}</p>
        </Card>

        <Card
          title="Documents the release needs"
          description={
            c.legal_documents_mode === "required"
              ? "The vault opens only after a reviewer approves one of these."
              : "Advisory here: shown to nominees, but not blocking release."
          }
          actions={
            caseItem.mode === "plan" ? (
              <ActionButton
                size="sm"
                onAction={async () => {
                  const next = c.legal_documents_mode === "required" ? "advisory" : "required";
                  await api.put(casePath(caseId, "/legacy/policy"), { legal_documents: next });
                  changed();
                  return `Legal documents are now ${next}.`;
                }}
              >
                Make {c.legal_documents_mode === "required" ? "advisory" : "required"}
              </ActionButton>
            ) : undefined
          }
        >
          {p.legal_requirements.map((r) => (
            <div key={r.key} className="stack-sm">
              <div className="item-head">
                <strong>{r.label}</strong>
                <Badge tone={c.legal_documents_mode === "required" ? "warning" : "neutral"}>
                  {humanize(c.legal_documents_mode)}
                </Badge>
              </div>
              <p className="muted">{r.why}</p>
              <p className="hint">Any one of:</p>
              <ul>
                {r.accepted.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          ))}
        </Card>
      </div>

      <Card
        title="Key deadlines"
        description={
          c.date_of_death
            ? `Calculated from the date of death, ${formatDate(c.date_of_death)}.`
            : "Dates appear once a death certificate or death claim gives the date of death."
        }
      >
        {c.deadlines.length === 0 ? (
          <p className="muted">No standard deadlines are listed for this country. Ask a local lawyer.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Deadlines for {p.name}</caption>
              <thead>
                <tr>
                  <th scope="col">What</th>
                  <th scope="col">Due</th>
                  <th scope="col">Details</th>
                </tr>
              </thead>
              <tbody>
                {c.deadlines.map((d) => (
                  <tr key={d.label}>
                    <th scope="row">{d.label}</th>
                    <td>
                      {d.due ? (
                        <span className="stack-sm">
                          <time dateTime={d.due}>{formatDate(d.due)}</time>
                          {d.overdue ? (
                            <Badge tone="danger">Overdue</Badge>
                          ) : d.days_left !== null ? (
                            <Badge tone={d.days_left <= 14 ? "warning" : "info"}>{plural(d.days_left, "day")} left</Badge>
                          ) : null}
                        </span>
                      ) : (
                        <span className="muted">Depends on the case</span>
                      )}
                    </td>
                    <td>{d.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="Privacy law" description={p.privacy.law}>
          <dl className="facts">
            <dt>Regulator</dt>
            <dd>{p.privacy.regulator}</dd>
            <dt>Retention</dt>
            <dd>{p.privacy.retention}</dd>
            <dt>Transfers abroad</dt>
            <dd>{p.privacy.transfers}</dd>
            <dt>After death</dt>
            <dd>{p.privacy.deceased_data}</dd>
            <dt>Operator duty</dt>
            <dd>{p.privacy.contact_requirement}</dd>
          </dl>
          <h3>Rights of owners and nominees</h3>
          <ul>
            {p.privacy.rights.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <details className="disclosure">
            <summary>Privacy notice shown to nominees</summary>
            <p className="disclosure-body">{p.privacy.notice}</p>
          </details>
          <div className="button-row" style={{ marginTop: "0.75rem" }}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => go("overview")}>
              Export or delete this case's data
            </button>
          </div>
        </Card>

        <Card title="Where the data is stored">
          <div className={`callout ${residency.matches ? "callout-success" : "callout-warning"}`} role={residency.matches ? undefined : "alert"}>
            <p>
              <span aria-hidden="true">{residency.matches ? "✓ " : "⚠ "}</span>
              {residency.deployment_region
                ? `This deployment stores data in region "${residency.deployment_region}". `
                : "This deployment has not declared a data region (set TROVE_DATA_REGION). "}
              Recommended for {p.name}: "{residency.recommended_region}".
              {residency.enforced ? " Residency is enforced: cases for other regions are refused." : ""}
            </p>
          </div>
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            {residency.note}
          </p>
        </Card>
      </div>

      {Object.keys(p.official_tracing).length > 0 && (
        <Card title="Official places to trace assets" description={`Free or official search services in ${p.name}.`}>
          <dl className="facts">
            {Object.entries(p.official_tracing).map(([category, items]) => (
              <div key={category} className="facts-row">
                <dt>{humanize(category)}</dt>
                <dd>
                  <ul>
                    {items.map((i) => (
                      <li key={i}>{i}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      )}
    </div>
  );
}
