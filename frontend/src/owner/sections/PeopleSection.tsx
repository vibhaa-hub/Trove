import { enc } from "../../shared/api";
import { ActionButton } from "../../shared/components/ActionButton";
import { Button } from "../../shared/components/Button";
import { useConfirm } from "../../shared/components/Dialog";
import { Badge, Card, EmptyState, ErrorNote, Skeleton, StatusBadge } from "../../shared/components/Display";
import { FormError, TextField } from "../../shared/components/Field";
import { str, useFormSubmit } from "../../shared/components/useFormSubmit";
import { humanize, percent } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { Beneficiary, EntityMatch, RelationshipGraph } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

export function PeopleSection() {
  const { caseId, version, changed } = useCase();
  const confirm = useConfirm();
  const beneficiaries = useResource(
    (s) => api.get<{ beneficiaries: Beneficiary[] }>(casePath(caseId, "/beneficiaries"), s),
    [caseId, version],
  );
  const matches = useResource(
    (s) => api.get<{ suggestions: EntityMatch[] }>(casePath(caseId, "/people/entity-resolution"), s),
    [caseId, version],
  );
  const graph = useResource((s) => api.get<RelationshipGraph>(casePath(caseId, "/relationships"), s), [caseId, version]);

  const form = useFormSubmit(async (fd) => {
    const body: Record<string, unknown> = { name: str(fd, "name"), relationship: str(fd, "relationship") };
    if (str(fd, "share_percent")) body.share_percent = Number(fd.get("share_percent"));
    await api.post(casePath(caseId, "/beneficiaries"), body);
    changed();
    return "Beneficiary added. It stays unverified until you record a document review.";
  });

  const shareTotal = (beneficiaries.data?.beneficiaries ?? []).reduce((sum, b) => sum + (b.share_percent ?? 0), 0);

  return (
    <div className="stack">
      <div className="grid grid-2">
        <Card title="Add a beneficiary record" description="As reported by the family. Trove never determines legal heirship.">
          <form className="stack" onSubmit={form.onSubmit}>
            <FormError message={form.error} />
            <TextField label="Name" name="name" required maxLength={200} autoComplete="off" />
            <TextField label="Relationship (as reported)" name="relationship" required maxLength={100} placeholder="child, spouse, trustee…" />
            <TextField label="Share" name="share_percent" type="number" min={0} max={100} step={0.01} optional hint="Percentage, 0–100" />
            <div className="button-row">
              <Button type="submit" variant="primary" busy={form.busy}>
                Add beneficiary
              </Button>
            </div>
          </form>
        </Card>
        <Card
          title="Beneficiaries"
          description={
            beneficiaries.data?.beneficiaries.length
              ? `Shares recorded: ${shareTotal}%${shareTotal !== 100 ? " (doesn't add up to 100%)" : ""}`
              : undefined
          }
        >
          {beneficiaries.error ? (
            <ErrorNote message={beneficiaries.error} onRetry={beneficiaries.reload} />
          ) : !beneficiaries.data ? (
            <Skeleton />
          ) : beneficiaries.data.beneficiaries.length === 0 ? (
            <EmptyState title="No beneficiaries recorded" />
          ) : (
            <ul className="item-list">
              {beneficiaries.data.beneficiaries.map((b) => (
                <li key={b.id} className="item">
                  <div className="item-head">
                    <span className="item-title">{b.name}</span>
                    <StatusBadge status={b.verification_status} />
                  </div>
                  <p className="item-meta">
                    {humanize(b.relationship)} · {humanize(b.beneficiary_type)} · {b.share_percent != null ? `${b.share_percent}%` : "share not set"}
                  </p>
                  {b.verification_status === "unverified" && (
                    <div className="item-actions">
                      <ActionButton
                        size="sm"
                        aria-label={`Record document review for ${b.name}`}
                        onAction={async () => {
                          const reviewer = await confirm({
                            title: `Record document review for ${b.name}`,
                            message: "You confirm you've seen documents supporting this relationship. This is not legal heir verification.",
                            confirmLabel: "Record review",
                            input: { label: "Your name (for the audit record)", autoComplete: "name" },
                          });
                          if (!reviewer) return;
                          await api.patch(casePath(caseId, `/beneficiaries/${enc(b.id)}`), { decision: "documents_reviewed", reviewer });
                          changed();
                          return "Document review recorded.";
                        }}
                      >
                        Record document review
                      </ActionButton>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Possible duplicate records" description="Suggestions only. Trove never merges people automatically.">
          {!matches.data ? (
            <Skeleton lines={2} />
          ) : matches.data.suggestions.length === 0 ? (
            <p className="muted">No likely duplicate names found.</p>
          ) : (
            <ul className="item-list">
              {matches.data.suggestions.map((m, i) => (
                <li key={i} className="item">
                  <span className="item-title">
                    {m.person_a.full_name} ↔ {m.person_b.full_name}
                  </span>{" "}
                  <Badge tone="warning">{percent(m.similarity)} similar</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Relationships" description="Links between people, assets, documents and claims.">
          {!graph.data ? (
            <Skeleton lines={2} />
          ) : graph.data.edges.length === 0 ? (
            <p className="muted">No relationships recorded yet.</p>
          ) : (
            <ul className="item-list">
              {graph.data.edges.map((edge, i) => {
                const label = (id: string) => graph.data!.nodes.find((n) => n.id === id)?.label ?? "Unknown";
                return (
                  <li key={i} className="item">
                    {label(edge.source)} <span aria-hidden="true">→</span>
                    <span className="sr-only"> is </span> <strong>{humanize(edge.label)}</strong> <span aria-hidden="true">→</span>
                    <span className="sr-only"> of </span> {label(edge.target)} <StatusBadge status={edge.status} />
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
