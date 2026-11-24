import { ActionButton } from "../../shared/components/ActionButton";
import { Button } from "../../shared/components/Button";
import { useConfirm } from "../../shared/components/Dialog";
import { Card, EmptyState, ErrorNote, Skeleton, Stat, StatusBadge } from "../../shared/components/Display";
import { useToast } from "../../shared/components/Toaster";
import { errorMessage, enc } from "../../shared/api";
import { humanize, WORKFLOW_STAGES } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { Asset, Workflow } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

function advanceBlocker(w: Workflow): string | null {
  if (w.can_advance) return null;
  if (w.stage === WORKFLOW_STAGES[WORKFLOW_STAGES.length - 1]) return "This case is at the final stage.";
  if (w.assets_needing_review) return `Review ${w.assets_needing_review} asset lead(s) first.`;
  if (w.stage === "intake") return "Upload a death certificate in Documents to continue.";
  if (w.stage === "death_verification") return "Review the death certificate in Documents to continue.";
  if (w.unverified_beneficiaries) return "Record a document review for every beneficiary in People.";
  return "Complete the current stage first.";
}

export function OverviewSection() {
  const { caseId, caseItem, version, changed, go } = useCase();
  const toast = useToast();
  const confirm = useConfirm();
  const workflow = useResource((s) => api.get<Workflow>(casePath(caseId, "/workflow"), s), [caseId, version]);
  const assets = useResource((s) => api.get<{ assets: Asset[] }>(casePath(caseId, "/assets"), s), [caseId, version]);

  const w = workflow.data;
  const stageIndex = w ? WORKFLOW_STAGES.indexOf(w.stage as (typeof WORKFLOW_STAGES)[number]) : -1;
  const blocker = w ? advanceBlocker(w) : null;

  async function exportCase() {
    const data = await api.get<unknown>(casePath(caseId, "/export"));
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `trove-case-${caseId}.json`;
    link.click();
    URL.revokeObjectURL(url);
    return "Case export downloaded.";
  }

  async function deleteCase() {
    const answer = await confirm({
      title: "Delete this case permanently?",
      danger: true,
      confirmLabel: "Delete case",
      message: (
        <p>
          This deletes every record, document and audit entry for <strong>{caseItem.subject_name}</strong>. It cannot be undone.
          Type <strong>DELETE</strong> to confirm.
        </p>
      ),
      input: { label: "Type DELETE to confirm", mustEqual: "DELETE" },
    });
    if (answer !== "DELETE") return;
    try {
      await api.del(casePath(caseId), { confirmation: "DELETE" });
      toast.success("Case and its records were deleted.");
      changed();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (workflow.error) return <ErrorNote message={workflow.error} onRetry={workflow.reload} />;

  const pending = (assets.data?.assets ?? []).filter((a) => a.status === "needs_review");

  return (
    <div className="stack">
      {!w ? (
        <Skeleton lines={3} label="Loading case summary" />
      ) : (
        <dl className="stats">
          <Stat label="Mode" value={caseItem.mode === "plan" ? "Plan" : "Discovery"} hint={caseItem.jurisdiction} tone="info" />
          <Stat label="Assets on record" value={w.asset_count} />
          <Stat
            label="Leads to review"
            value={w.assets_needing_review}
            tone={w.assets_needing_review ? "warning" : "success"}
          />
          <Stat label="Death review" value={humanize(w.death_status)} tone={w.death_status === "reviewed" ? "success" : "neutral"} />
          <Stat
            label="Unverified beneficiaries"
            value={w.unverified_beneficiaries}
            tone={w.unverified_beneficiaries ? "warning" : "success"}
          />
        </dl>
      )}

      <Card
        title="Estate workflow"
        description="Each stage unlocks when the one before it is complete."
        actions={
          w && (
            <ActionButton
              variant="primary"
              disabled={!w.can_advance}
              aria-describedby={blocker ? "advance-blocker" : undefined}
              onAction={async () => {
                const result = await api.post<{ stage: string }>(casePath(caseId, "/workflow/advance"));
                changed();
                return `Moved to ${humanize(result.stage)}.`;
              }}
            >
              Advance to next stage
            </ActionButton>
          )
        }
      >
        {w && (
          <>
            <ol className="timeline" aria-label="Workflow stages">
              {WORKFLOW_STAGES.map((stage, i) => (
                <li
                  key={stage}
                  className={i < stageIndex ? "done" : i === stageIndex ? "current" : ""}
                  aria-current={i === stageIndex ? "step" : undefined}
                >
                  {humanize(stage)}
                  <span className="sr-only">{i < stageIndex ? " (complete)" : i === stageIndex ? " (current stage)" : ""}</span>
                </li>
              ))}
            </ol>
            {blocker && (
              <p id="advance-blocker" className="hint" style={{ marginTop: "0.75rem" }}>
                <span aria-hidden="true">ⓘ </span>
                {blocker}
              </p>
            )}
          </>
        )}
      </Card>

      <Card
        title="Asset leads awaiting your review"
        description="Leads come from documents, emails and Discovery sources. Confirm only what you can corroborate."
        busy={assets.loading}
      >
        {!assets.data ? (
          <Skeleton />
        ) : pending.length === 0 ? (
          <EmptyState title="Nothing to review">
            {assets.data.assets.length
              ? `All ${assets.data.assets.length} asset record(s) are reviewed.`
              : "Add a document or a Discovery source to find assets."}
          </EmptyState>
        ) : (
          <ul className="item-list">
            {pending.map((asset) => (
              <AssetRow key={asset.id} asset={asset} />
            ))}
          </ul>
        )}
        {assets.data && assets.data.assets.length > pending.length && (
          <details className="disclosure" style={{ marginTop: "1rem" }}>
            <summary>All assets ({assets.data.assets.length})</summary>
            <div className="disclosure-body">
              <ul className="item-list">
                {assets.data.assets
                  .filter((a) => a.status !== "needs_review")
                  .map((asset) => (
                    <AssetRow key={asset.id} asset={asset} />
                  ))}
              </ul>
            </div>
          </details>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="Quick actions" level={2}>
          <div className="button-row">
            <Button onClick={() => go(caseItem.mode === "plan" ? "plan" : "discovery")} variant="primary">
              Open {caseItem.mode === "plan" ? "Plan" : "Discovery"} mode
            </Button>
            <Button onClick={() => go("documents")}>Upload a document</Button>
            <Button onClick={() => go("assistant")}>Ask the assistant</Button>
          </div>
        </Card>
        <Card title="Your data" description="Download everything held for this case, or erase it." className="danger-zone">
          <div className="button-row">
            <ActionButton onAction={exportCase}>Export case data (JSON)</ActionButton>
            <Button variant="danger" onClick={deleteCase}>
              Delete case…
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function AssetRow({ asset }: { asset: Asset }) {
  const { caseId, changed } = useCase();
  const details = [asset.institution, asset.masked_reference, asset.description].filter(Boolean).join(" · ");
  const name = `${humanize(asset.asset_type)}${asset.institution ? ` at ${asset.institution}` : ""}`;
  const decide = (decision: "confirmed" | "rejected") => async () => {
    await api.patch(casePath(caseId, `/assets/${enc(asset.id)}`), { decision });
    changed();
    return `${name}: ${decision}.`;
  };
  return (
    <li className="item">
      <div className="item-head">
        <span className="item-title">{humanize(asset.asset_type)}</span>
        <StatusBadge status={asset.status} />
      </div>
      {details && <p className="item-meta">{details}</p>}
      {asset.status === "needs_review" && (
        <div className="item-actions">
          <ActionButton size="sm" variant="primary" onAction={decide("confirmed")} aria-label={`Confirm ${name}`}>
            Confirm
          </ActionButton>
          <ActionButton size="sm" variant="danger" onAction={decide("rejected")} aria-label={`Reject ${name}`}>
            Reject
          </ActionButton>
        </div>
      )}
    </li>
  );
}
