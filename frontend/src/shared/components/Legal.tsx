import type { ReactNode } from "react";
import { formatDate } from "../format";
import type { LegalDocument, LegalStatus } from "../types";
import { Badge, StatusBadge } from "./Display";

/**
 * Country-specific legal documents on a release request: what is required, what was uploaded,
 * and its review status. `documentActions` / `uploadSlot` let each portal add its own controls.
 */
export function LegalRequirements({
  legal,
  documentActions,
  uploadSlot,
  headingLevel = 3,
}: {
  legal: LegalStatus;
  documentActions?: (doc: LegalDocument) => ReactNode;
  uploadSlot?: (requirementKey: string, accepted: string[]) => ReactNode;
  headingLevel?: 3 | 4;
}) {
  const Heading = headingLevel === 3 ? "h3" : "h4";
  return (
    <section className="legal" aria-label={`Legal documents for ${legal.country_name}`}>
      <div className="item-head">
        <Heading>Legal documents · {legal.country_name}</Heading>
        {legal.mode === "required" ? (
          <Badge tone={legal.satisfied >= legal.total ? "success" : "warning"}>
            Required · {legal.satisfied}/{legal.total} approved
          </Badge>
        ) : (
          <Badge tone="neutral">Advisory · not blocking release</Badge>
        )}
      </div>
      <ul className="item-list">
        {legal.requirements.map((req) => {
          const docs = legal.documents.filter((d) => d.requirement_key === req.key);
          return (
            <li key={req.key} className="item">
              <div className="item-head">
                <span className="item-title">{req.label}</span>
                {req.satisfied ? (
                  <Badge tone="success">Approved</Badge>
                ) : req.pending ? (
                  <Badge tone="info">Under review</Badge>
                ) : (
                  <Badge tone={legal.mode === "required" ? "warning" : "neutral"}>Needed</Badge>
                )}
              </div>
              <p className="item-meta">{req.why}</p>
              <details className="disclosure">
                <summary>Accepted documents ({req.accepted.length})</summary>
                <ul className="disclosure-body">
                  {req.accepted.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </details>
              {docs.length > 0 && (
                <ul className="item-list legal-docs">
                  {docs.map((doc) => (
                    <li key={doc.id} className="legal-doc">
                      <div className="item-head">
                        <span>
                          {doc.document_type}
                          {doc.file_name && <span className="muted"> · {doc.file_name}</span>}
                        </span>
                        <StatusBadge status={doc.status} />
                      </div>
                      <p className="item-meta">Uploaded {formatDate(doc.created_at)}</p>
                      {documentActions?.(doc)}
                    </li>
                  ))}
                </ul>
              )}
              {!req.satisfied && uploadSlot?.(req.key, req.accepted)}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
