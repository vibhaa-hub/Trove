import { useId } from "react";
import { enc, errorMessage } from "../../shared/api";
import { Button } from "../../shared/components/Button";
import { Card, EmptyState, ErrorNote, Skeleton, StatusBadge } from "../../shared/components/Display";
import { FormError, SelectField, TextField } from "../../shared/components/Field";
import { useToast } from "../../shared/components/Toaster";
import { str, useFormSubmit } from "../../shared/components/useFormSubmit";
import { CLAIM_STATUSES, formatDate, humanize } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { Asset, Claim, Workflow } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

export function ClaimsSection() {
  const { caseId, version, changed, go } = useCase();
  const claims = useResource((s) => api.get<{ claims: Claim[] }>(casePath(caseId, "/claims"), s), [caseId, version]);
  const assets = useResource((s) => api.get<{ assets: Asset[] }>(casePath(caseId, "/assets"), s), [caseId, version]);
  const workflow = useResource((s) => api.get<Workflow>(casePath(caseId, "/workflow"), s), [caseId, version]);
  const open = workflow.data ? ["claims", "settlement"].includes(workflow.data.stage) : false;

  const form = useFormSubmit(async (fd) => {
    const body: Record<string, string> = { next_action: str(fd, "next_action") };
    if (str(fd, "asset_id")) body.asset_id = str(fd, "asset_id");
    if (str(fd, "due_date")) body.due_date = str(fd, "due_date");
    await api.post(casePath(caseId, "/claims"), body);
    changed();
    return "Claim tracker created.";
  });

  return (
    <div className="grid grid-2">
      <Card title="Track a claim" description="One tracker per institution you need to contact.">
        {workflow.data && !open && (
          <div className="callout callout-warning" style={{ marginBottom: "1rem" }}>
            <p>
              Claims open once the case reaches the <strong>Claims</strong> stage (now: {humanize(workflow.data.stage)}).{" "}
              <button type="button" className="link-button" onClick={() => go("overview")}>
                See what's blocking
              </button>
            </p>
          </div>
        )}
        <form className="stack" onSubmit={form.onSubmit}>
          <FormError message={form.error} />
          <SelectField
            label="Related asset"
            name="asset_id"
            optional
            disabled={!open}
            options={[
              { value: "", label: "No linked asset" },
              ...(assets.data?.assets ?? []).map((a) => ({
                value: a.id,
                label: `${humanize(a.asset_type)} · ${a.institution ?? "institution unknown"}`,
              })),
            ]}
          />
          <TextField label="Next action" name="next_action" maxLength={500} placeholder="e.g. Request claim forms" disabled={!open} />
          <TextField label="Due date" name="due_date" type="date" optional disabled={!open} />
          <div className="button-row">
            <Button type="submit" variant="primary" busy={form.busy} disabled={!open}>
              Create claim tracker
            </Button>
          </div>
        </form>
      </Card>
      <Card title="Claims" busy={claims.loading}>
        {claims.error ? (
          <ErrorNote message={claims.error} onRetry={claims.reload} />
        ) : !claims.data ? (
          <Skeleton />
        ) : claims.data.claims.length === 0 ? (
          <EmptyState title="No claims yet" />
        ) : (
          <ul className="item-list">
            {claims.data.claims.map((c) => (
              <ClaimRow key={c.id} claim={c} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ClaimRow({ claim }: { claim: Claim }) {
  const { caseId, changed } = useCase();
  const toast = useToast();
  const id = useId();
  const name = claim.institution ?? "Institution not set";
  return (
    <li className="item">
      <div className="item-head">
        <span className="item-title">{name}</span>
        <StatusBadge status={claim.status} />
      </div>
      <p className="item-meta">
        {claim.next_action || "No next action"}
        {claim.due_date && <> · due {formatDate(claim.due_date)}</>}
      </p>
      <div className="field" style={{ marginTop: "0.6rem", maxWidth: "18rem" }}>
        <label htmlFor={id}>
          Status<span className="sr-only"> for {name}</span>
        </label>
        <select
          id={id}
          defaultValue={claim.status}
          onChange={async (e) => {
            try {
              await api.patch(casePath(caseId, `/claims/${enc(claim.id)}`), { status: e.target.value });
              toast.success(`${name}: ${humanize(e.target.value)}.`);
              changed();
            } catch (error) {
              toast.error(errorMessage(error));
              e.target.value = claim.status;
            }
          }}
        >
          {CLAIM_STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </select>
      </div>
    </li>
  );
}
