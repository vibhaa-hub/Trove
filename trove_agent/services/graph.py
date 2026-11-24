"""Per-case knowledge graph (Trove.txt section 11).

    PERSON ── EMAIL / PHONE ── (shared contact detection)
       │
       ├── HOLDS ──> ASSET ── AT ──> INSTITUTION
       │               └── EVIDENCED_BY ──> SOURCE / DOCUMENT
       ├── NOMINATED ──> PERSON (nominee)
       └── BENEFICIARY ──> PERSON

The graph is rebuilt from the relational tables on demand and persisted as a
snapshot (kg_nodes / kg_edges) so it can be exported (e.g. to Neo4j) or
queried. Contacts are keyed by blind index; labels are masked. Insights:

* hidden relationships: one phone/email linked to several people (fraud)
* entity resolution: institution or person variants (encoder similarity)
* coverage: strong leads not in the asset register, institutions with no
  claim tracker, beneficiaries who are not verified nominees
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from trove_agent.agents.encoders import cosine, get_encoder
from trove_agent.core.store import TroveService, new_id, now


def _norm_institution(name: str) -> str:
    name = re.sub(r"\b(ltd|limited|plc|inc|co|corp|corporation|the|bank)\b", " ", name.casefold())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


class KnowledgeGraph:
    def __init__(self, service: TroveService) -> None:
        from trove_agent.discovery.bootstrap import DiscoveryBootstrap
        from trove_agent.services.legacy import LegacyService

        self.service = service
        LegacyService(service)        # ensures nominee tables exist
        DiscoveryBootstrap(service)   # ensures discovery tables exist
        with service._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    node_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    props_json TEXT NOT NULL,
                    UNIQUE(case_id, node_key)
                );
                CREATE TABLE IF NOT EXISTS kg_edges (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    src_key TEXT NOT NULL,
                    dst_key TEXT NOT NULL,
                    rel TEXT NOT NULL,
                    props_json TEXT NOT NULL,
                    UNIQUE(case_id, src_key, dst_key, rel)
                );
                CREATE TABLE IF NOT EXISTS kg_meta (
                    case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
                    built_at TEXT NOT NULL
                );
                """
            )

    def build(self, case_id: str) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}

        def node(key: str, kind: str, label: str, **props: Any) -> str:
            nodes.setdefault(key, {"key": key, "kind": kind, "label": label, "props": props})
            return key

        def edge(src: str, dst: str, rel: str, **props: Any) -> None:
            edges.setdefault((src, dst, rel), {"src": src, "dst": dst, "rel": rel, "props": props})

        with self.service._connect() as db:
            case = self.service._case_row(db, case_id)
            subject = node("person:subject", "PERSON", "Case subject", role="subject")
            for n in db.execute(
                    "SELECT id, full_name, role, status, email_bidx, phone_bidx, email, phone "
                    "FROM nominees WHERE case_id = ?", (case_id,)).fetchall():
                key = node(f"person:nominee:{n['id']}", "PERSON", n["full_name"],
                           role=n["role"], status=n["status"])
                edge(subject, key, "NOMINATED")
                email = self.service.decrypt_field(n["email"]) or ""
                phone = self.service.decrypt_field(n["phone"]) or ""
                if n["email_bidx"]:
                    edge(key, node(f"email:{n['email_bidx'][:20]}", "EMAIL",
                                   f"{email[:1]}***@{email.split('@')[-1]}"), "HAS_EMAIL")
                if n["phone_bidx"]:
                    edge(key, node(f"phone:{n['phone_bidx'][:20]}", "PHONE", f"***{phone[-3:]}"),
                         "HAS_PHONE")
            for s in db.execute("SELECT kind, masked, value_hash FROM discovery_seeds WHERE case_id = ?",
                                (case_id,)).fetchall():
                if s["kind"] in ("email", "phone"):
                    kind = s["kind"].upper()
                    edge(subject, node(f"{s['kind']}:{s['value_hash'][:20]}", kind, s["masked"]),
                         f"HAS_{kind}", source="family_seed")
                elif s["kind"] == "employer":
                    edge(subject, node(f"org:employer:{_norm_institution(s['masked'])}", "EMPLOYER",
                                       s["masked"]), "EMPLOYED_BY")
            for b in db.execute("SELECT id, name, relationship, share_percent FROM beneficiaries "
                                "WHERE case_id = ?", (case_id,)).fetchall():
                key = node(f"person:beneficiary:{b['id']}", "PERSON", b["name"],
                           role="beneficiary", relationship=b["relationship"])
                edge(subject, key, "BENEFICIARY", share=b["share_percent"])
            for a in db.execute("SELECT id, asset_type, institution, status, masked_reference "
                                "FROM assets WHERE case_id = ?", (case_id,)).fetchall():
                key = node(f"asset:{a['id']}", "ASSET", a["asset_type"], status=a["status"],
                           ref=a["masked_reference"], registered=True)
                edge(subject, key, "HOLDS")
                if a["institution"]:
                    edge(key, node(f"institution:{_norm_institution(a['institution'])}",
                                   "INSTITUTION", a["institution"]), "AT")
            for lead in db.execute("SELECT * FROM discovery_leads WHERE case_id = ? "
                                   "AND status != 'dismissed'", (case_id,)).fetchall():
                if lead["asset_id"]:
                    continue  # already represented by its asset
                key = node(f"lead:{lead['id']}", "ASSET", lead["asset_type"], status="lead",
                           registered=False, sources=len(json.loads(lead["source_ids_json"])))
                edge(subject, key, "POSSIBLY_HOLDS")
                if lead["institution"] and not lead["institution"].startswith("Employer:"):
                    edge(key, node(f"institution:{_norm_institution(lead['institution'])}",
                                   "INSTITUTION", lead["institution"]), "AT")
                for source_id in json.loads(lead["source_ids_json"]):
                    edge(key, node(f"source:{source_id}", "SOURCE", "discovery source"), "EVIDENCED_BY")
            for d in db.execute("SELECT id, file_name, category FROM documents WHERE case_id = ?",
                                (case_id,)).fetchall():
                node(f"document:{d['id']}", "DOCUMENT", d["category"], file=d["file_name"])
            claims = {(_norm_institution(r["institution"] or "")) for r in db.execute(
                "SELECT institution FROM claims WHERE case_id = ?", (case_id,)).fetchall()}
            # persist snapshot
            db.execute("DELETE FROM kg_edges WHERE case_id = ?", (case_id,))
            db.execute("DELETE FROM kg_nodes WHERE case_id = ?", (case_id,))
            for item in nodes.values():
                db.execute("INSERT INTO kg_nodes (id, case_id, node_key, kind, label, props_json) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (new_id(), case_id, item["key"], item["kind"], item["label"],
                            json.dumps(item["props"], default=str)))
            for item in edges.values():
                db.execute("INSERT INTO kg_edges (id, case_id, src_key, dst_key, rel, props_json) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (new_id(), case_id, item["src"], item["dst"], item["rel"],
                            json.dumps(item["props"], default=str)))
            db.execute("""INSERT INTO kg_meta (case_id, built_at) VALUES (?, ?)
                          ON CONFLICT(case_id) DO UPDATE SET built_at = excluded.built_at""",
                       (case_id, now()))
        return {"case_id": case_id, "mode": case["mode"],
                "nodes": list(nodes.values()), "edges": list(edges.values()),
                "insights": self._insights(nodes, edges, claims)}

    # -------------------------------------------------------------- insights
    def _insights(self, nodes: dict, edges: dict, claimed: set[str]) -> list[dict[str, Any]]:
        insights: list[dict[str, Any]] = []
        owners: dict[str, set[str]] = {}
        for (src, dst, rel) in edges:
            if rel in ("HAS_EMAIL", "HAS_PHONE"):
                owners.setdefault(dst, set()).add(src)
        for contact, people in owners.items():
            nominees = [p for p in people if p.startswith("person:nominee:")]
            if len(nominees) > 1:
                insights.append({
                    "type": "shared_contact", "severity": "high",
                    "message": f"{len(nominees)} nominees share {nodes[contact]['kind'].lower()} "
                               f"{nodes[contact]['label']} — their verifications are not independent.",
                    "nodes": sorted(nominees) + [contact]})
            elif nominees and "person:subject" in people:
                insights.append({
                    "type": "subject_contact_on_nominee", "severity": "medium",
                    "message": f"A nominee registered the deceased's own {nodes[contact]['kind'].lower()} "
                               f"{nodes[contact]['label']}.",
                    "nodes": nominees + [contact]})

        institutions = [n for n in nodes.values() if n["kind"] == "INSTITUTION"]
        if len(institutions) > 1:
            vectors = get_encoder().embed([n["label"] for n in institutions])
            for i in range(len(institutions)):
                for j in range(i + 1, len(institutions)):
                    a, b = institutions[i], institutions[j]
                    ratio = difflib.SequenceMatcher(None, _norm_institution(a["label"]),
                                                    _norm_institution(b["label"])).ratio()
                    if ratio >= 0.85 or cosine(vectors[i], vectors[j]) >= 0.93:
                        insights.append({
                            "type": "possible_duplicate_institution", "severity": "low",
                            "message": f"“{a['label']}” and “{b['label']}” may be the same institution.",
                            "nodes": [a["key"], b["key"]]})

        people = [n for n in nodes.values() if n["kind"] == "PERSON" and n["key"] != "person:subject"]
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                a, b = people[i], people[j]
                if a["props"].get("role") == b["props"].get("role"):
                    continue
                if difflib.SequenceMatcher(None, a["label"].casefold(), b["label"].casefold()).ratio() >= 0.85:
                    insights.append({
                        "type": "possible_same_person", "severity": "info",
                        "message": f"“{a['label']}” ({a['props'].get('role')}) and “{b['label']}” "
                                   f"({b['props'].get('role')}) look like the same person — link, don't merge.",
                        "nodes": [a["key"], b["key"]]})

        unregistered = [n for n in nodes.values() if n["kind"] == "ASSET"
                        and not n["props"].get("registered") and n["props"].get("sources", 0) >= 2]
        if unregistered:
            insights.append({"type": "corroborated_leads_not_registered", "severity": "medium",
                             "message": f"{len(unregistered)} lead(s) seen in 2+ sources are not yet in "
                                        "the asset register.", "nodes": [n["key"] for n in unregistered]})
        unclaimed = sorted({nodes[dst]["label"] for (src, dst, rel) in edges
                            if rel == "AT" and nodes[src]["props"].get("status") == "confirmed"
                            and _norm_institution(nodes[dst]["label"]) not in claimed})
        if unclaimed:
            insights.append({"type": "confirmed_assets_without_claim", "severity": "medium",
                             "message": "Confirmed assets with no claim tracker: " + ", ".join(unclaimed),
                             "nodes": []})
        return insights
