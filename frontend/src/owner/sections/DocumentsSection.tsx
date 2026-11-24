import { useState } from "react";
import { takeFile, enc, fileToBase64 } from "../../shared/api";
import { ActionButton } from "../../shared/components/ActionButton";
import { Button } from "../../shared/components/Button";
import { useConfirm } from "../../shared/components/Dialog";
import { Card, EmptyState, ErrorNote, Skeleton, StatusBadge } from "../../shared/components/Display";
import { FileField, FormError, SelectField, TextField } from "../../shared/components/Field";
import { str, useFormSubmit } from "../../shared/components/useFormSubmit";
import { DOCUMENT_CATEGORIES, formatDate, plural } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { DocumentItem } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

export function DocumentsSection() {
  const { caseId, version, changed } = useCase();
  const [category, setCategory] = useState("death_certificate");
  const docs = useResource((s) => api.get<{ documents: DocumentItem[] }>(casePath(caseId, "/documents"), s), [caseId, version]);

  const upload = useFormSubmit(async (fd) => {
    const file = takeFile(fd, "file", "Document");
    const body: Record<string, string> = {
      file_name: file.name,
      category: str(fd, "category"),
      content_base64: await fileToBase64(file),
    };
    if (body.category === "death_certificate" && str(fd, "date_of_death")) body.date_of_death = str(fd, "date_of_death");
    const result = await api.post<{ assets_created: unknown[] }>(casePath(caseId, "/documents"), body);
    changed();
    setCategory("death_certificate");
    return `Encrypted and saved. ${plural(result.assets_created.length, "possible asset lead")} found — review in Overview.`;
  });

  return (
    <div className="grid grid-2">
      <Card title="Upload a document" description="Files are encrypted in the vault. Certificate review is a workflow step, not legal verification.">
        <form className="stack" onSubmit={upload.onSubmit}>
          <FormError message={upload.error} />
          <SelectField
            label="Document type"
            name="category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            options={Object.entries(DOCUMENT_CATEGORIES).map(([value, label]) => ({ value, label }))}
          />
          {category === "death_certificate" && <TextField label="Date of death" name="date_of_death" type="date" optional />}
          <FileField label="File" name="file" required accept=".txt,.eml,.csv,.json,.pdf" hint="TXT, EML, CSV, JSON or text-based PDF, up to 5 MB." />
          <div className="button-row">
            <Button type="submit" variant="primary" busy={upload.busy} busyLabel="Encrypting…">
              Upload document
            </Button>
          </div>
        </form>
      </Card>

      <Card title="Vault documents" busy={docs.loading}>
        {docs.error ? (
          <ErrorNote message={docs.error} onRetry={docs.reload} />
        ) : !docs.data ? (
          <Skeleton />
        ) : docs.data.documents.length === 0 ? (
          <EmptyState title="No documents yet">Start with a death certificate, a will, or any statement you have.</EmptyState>
        ) : (
          <ul className="item-list">
            {docs.data.documents.map((d) => (
              <DocumentRow key={d.id} doc={d} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function DocumentRow({ doc }: { doc: DocumentItem }) {
  const { caseId, changed } = useCase();
  const confirm = useConfirm();
  const pending = doc.review_status === "pending_review";
  const isCertificate = doc.category === "death_certificate";

  async function reviewCertificate(decision: "reviewed" | "rejected") {
    const reviewer = await confirm({
      title: decision === "reviewed" ? "Record certificate review" : "Reject death certificate",
      message:
        decision === "reviewed"
          ? "You confirm you have looked at this certificate. This is a workflow record, not legal verification."
          : "The case returns to intake; upload a corrected document to continue.",
      confirmLabel: decision === "reviewed" ? "Record review" : "Reject",
      danger: decision === "rejected",
      input: { label: "Your name (for the audit record)", autoComplete: "name" },
    });
    if (!reviewer) return;
    await api.post(casePath(caseId, "/documents/death-certificate"), { decision, reviewer });
    changed();
    return decision === "reviewed" ? "Certificate review recorded." : "Certificate rejected.";
  }

  return (
    <li className="item">
      <div className="item-head">
        <span className="item-title">{doc.file_name}</span>
        <StatusBadge status={doc.review_status} />
      </div>
      <p className="item-meta">
        {DOCUMENT_CATEGORIES[doc.category] ?? doc.category}
        {doc.created_at && <> · added {formatDate(doc.created_at)}</>} · SHA-256 <code title={doc.sha256}>{doc.sha256.slice(0, 12)}…</code>
      </p>
      <div className="item-actions">
        <a className="btn btn-secondary btn-sm" href={casePath(caseId, `/documents/${enc(doc.id)}/download`)}>
          Download<span className="sr-only"> {doc.file_name}</span>
        </a>
        {pending && isCertificate && (
          <>
            <ActionButton size="sm" variant="primary" onAction={() => reviewCertificate("reviewed")}>
              Mark reviewed
            </ActionButton>
            <ActionButton size="sm" variant="danger" onAction={() => reviewCertificate("rejected")}>
              Reject
            </ActionButton>
          </>
        )}
        {pending && !isCertificate && (
          <ActionButton
            size="sm"
            onAction={async () => {
              await api.post(casePath(caseId, `/documents/${enc(doc.id)}/review`), { decision: "reviewed" });
              changed();
              return `${doc.file_name} marked reviewed.`;
            }}
          >
            Mark reviewed
          </ActionButton>
        )}
        <ActionButton
          size="sm"
          variant="danger"
          aria-label={`Delete ${doc.file_name}`}
          onAction={async () => {
            const ok = await confirm({
              title: `Delete ${doc.file_name}?`,
              message: "The encrypted file and any review leads generated from it are permanently removed.",
              confirmLabel: "Delete document",
              danger: true,
            });
            if (!ok) return;
            await api.del(casePath(caseId, `/documents/${enc(doc.id)}`));
            changed();
            return "Document and its generated leads were deleted.";
          }}
        >
          Delete
        </ActionButton>
      </div>
    </li>
  );
}
