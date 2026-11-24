import { useMemo, useState } from "react";
import { ActionButton } from "../../shared/components/ActionButton";
import { Card, EmptyState, ErrorNote, Skeleton, StatusBadge } from "../../shared/components/Display";
import { humanize, plural } from "../../shared/format";
import { useResource } from "../../shared/hooks";
import type { KgEdge, KgNode, KnowledgeGraph } from "../../shared/types";
import { api, casePath } from "../api";
import { useCase } from "../CaseContext";

const WIDTH = 760;
const HEIGHT = 540;
const RINGS: Record<string, number> = {
  PERSON: 1,
  EMAIL: 1,
  PHONE: 1,
  EMPLOYER: 1,
  ASSET: 2,
  INSTITUTION: 3,
  DOCUMENT: 3,
  SOURCE: 3,
};
const RING_RADIUS = [0, 120, 200, 255];

interface Placed extends KgNode {
  x: number;
  y: number;
}

/** Deterministic radial layout: subject centred, people/contacts, then assets, then institutions. */
function layout(nodes: KgNode[]): Placed[] {
  const cx = WIDTH / 2;
  const cy = HEIGHT / 2;
  const byRing = new Map<number, KgNode[]>();
  for (const node of nodes) {
    const ring = node.key === "person:subject" ? 0 : RINGS[node.kind] ?? 3;
    byRing.set(ring, [...(byRing.get(ring) ?? []), node]);
  }
  const placed: Placed[] = [];
  for (const [ring, items] of byRing) {
    const offset = ring * 0.6;
    items.forEach((node, i) => {
      if (ring === 0) {
        placed.push({ ...node, x: cx, y: cy });
        return;
      }
      const angle = offset + (2 * Math.PI * i) / items.length;
      placed.push({
        ...node,
        x: cx + RING_RADIUS[ring] * Math.cos(angle) * 1.35,
        y: cy + RING_RADIUS[ring] * Math.sin(angle) * 0.95,
      });
    });
  }
  return placed;
}

function nodeLabel(node: KgNode): string {
  if (node.kind === "ASSET") return humanize(node.label);
  if (node.kind === "DOCUMENT") return humanize(node.label);
  return node.label;
}

export function GraphSection() {
  const { caseId, version } = useCase();
  const graph = useResource((s) => api.get<KnowledgeGraph>(casePath(caseId, "/graph"), s), [caseId, version]);
  const [hidden, setHidden] = useState<Set<string>>(new Set(["SOURCE"]));
  const [selected, setSelected] = useState<string | null>(null);

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of graph.data?.nodes ?? []) counts.set(n.kind, (counts.get(n.kind) ?? 0) + 1);
    return [...counts.entries()].sort();
  }, [graph.data]);

  const visible = useMemo(() => (graph.data?.nodes ?? []).filter((n) => !hidden.has(n.kind)), [graph.data, hidden]);
  const placed = useMemo(() => layout(visible), [visible]);
  const index = useMemo(() => new Map(placed.map((n) => [n.key, n])), [placed]);
  const edges = useMemo(
    () => (graph.data?.edges ?? []).filter((e) => index.has(e.src) && index.has(e.dst)),
    [graph.data, index],
  );
  const neighbours = useMemo(() => {
    if (!selected) return null;
    const set = new Set([selected]);
    for (const e of edges) {
      if (e.src === selected) set.add(e.dst);
      if (e.dst === selected) set.add(e.src);
    }
    return set;
  }, [selected, edges]);

  if (graph.error) return <ErrorNote message={graph.error} onRetry={graph.reload} />;
  const data = graph.data;

  return (
    <div className="stack">
      <Card
        title="Insights"
        description="Hidden relationships, duplicates and gaps found by linking every record."
        actions={
          <ActionButton
            onAction={async () => {
              await graph.reload();
              return "Graph rebuilt.";
            }}
          >
            Rebuild graph
          </ActionButton>
        }
      >
        {!data ? (
          <Skeleton lines={2} />
        ) : data.insights.length === 0 ? (
          <p className="muted">No hidden relationships or duplicates found.</p>
        ) : (
          <ul className="item-list">
            {data.insights.map((insight, i) => (
              <li key={i} className="item">
                <div className="item-head">
                  <span className="item-title">{humanize(insight.type)}</span>
                  <StatusBadge status={insight.severity} />
                </div>
                <p className="item-meta">{insight.message}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {data && data.nodes.length <= 1 ? (
        <EmptyState title="The graph is empty">Add nominees, documents or Discovery sources to see connections.</EmptyState>
      ) : data ? (
        <Card title="Map" description={`${plural(data.nodes.length, "node")} and ${plural(data.edges.length, "link")}.`}>
          <fieldset className="checks" style={{ marginBottom: "1rem" }}>
            <legend>Show</legend>
            <div className="checks-grid">
              {kinds.map(([kind, count]) => (
                <label key={kind} className="check">
                  <input
                    type="checkbox"
                    checked={!hidden.has(kind)}
                    onChange={(e) => {
                      const next = new Set(hidden);
                      if (e.target.checked) next.delete(kind);
                      else next.add(kind);
                      setHidden(next);
                      setSelected(null);
                    }}
                  />
                  <span>
                    <span className={`legend-swatch kind-${kind.toLowerCase()}`} aria-hidden="true" />
                    {humanize(kind)} ({count})
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <figure className="graph-figure">
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-labelledby="graph-caption">
              {edges.map((e) => {
                const a = index.get(e.src)!;
                const b = index.get(e.dst)!;
                const hl = selected !== null && (e.src === selected || e.dst === selected);
                return <line key={`${e.src}-${e.dst}-${e.rel}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`graph-edge ${hl ? "hl" : ""}`} />;
              })}
              {placed.map((n) => {
                const dim = neighbours !== null && !neighbours.has(n.key);
                const hl = n.key === selected;
                const r = n.key === "person:subject" ? 16 : n.kind === "ASSET" ? 10 : 8;
                return (
                  <g
                    key={n.key}
                    className={`graph-node ${dim ? "dim" : ""} ${hl ? "hl" : ""}`}
                    transform={`translate(${n.x},${n.y})`}
                    onClick={() => setSelected(hl ? null : n.key)}
                  >
                    <circle r={r} className={`kind-${n.kind.toLowerCase()}`} />
                    <text x={r + 4} y={4}>
                      {nodeLabel(n).slice(0, 26)}
                    </text>
                  </g>
                );
              })}
            </svg>
            <figcaption id="graph-caption">
              Network of {plural(placed.length, "visible node")} around the case subject. The table below lists the same information; choose
              a row to highlight its connections.
            </figcaption>
          </figure>

          <NodeTable nodes={placed} edges={edges} selected={selected} onSelect={setSelected} />
        </Card>
      ) : (
        <Skeleton lines={6} label="Building graph" />
      )}
    </div>
  );
}

function NodeTable({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: Placed[];
  edges: KgEdge[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const byKey = new Map(nodes.map((n) => [n.key, n]));
  return (
    <div className="table-wrap" style={{ marginTop: "1rem" }}>
      <table>
        <caption>Graph nodes and their connections</caption>
        <thead>
          <tr>
            <th scope="col">Node</th>
            <th scope="col">Kind</th>
            <th scope="col">Connected to</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n) => {
            const links = edges
              .filter((e) => e.src === n.key || e.dst === n.key)
              .map((e) => {
                const other = byKey.get(e.src === n.key ? e.dst : e.src);
                return other ? `${humanize(e.rel)}: ${nodeLabel(other)}` : null;
              })
              .filter(Boolean);
            const isSelected = selected === n.key;
            return (
              <tr key={n.key}>
                <th scope="row">
                  <button
                    type="button"
                    className="sort-button"
                    aria-pressed={isSelected}
                    onClick={() => onSelect(isSelected ? null : n.key)}
                  >
                    <span className={`legend-swatch kind-${n.kind.toLowerCase()}`} aria-hidden="true" />
                    {nodeLabel(n)}
                  </button>
                </th>
                <td>{humanize(n.kind)}</td>
                <td>{links.length ? links.join("; ") : <span className="muted">—</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
