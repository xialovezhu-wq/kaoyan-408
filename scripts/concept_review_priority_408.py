"""Read sealed point-review evidence for morning priority, never question memory."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

POLICY = "cs408-concept-priority-v1"
SNAPSHOT_REL = "support/d408/concept-review-evidence.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def point_memberships(master):
    return {fid: list(dict.fromkeys(re.findall(r"(?:DS|CO|OS|CN)\d{2}-\d{2}",
            " ".join(str(row.get(k) or "") for k in ("主知识点", "副知识点", "命中知识点")))))
            for fid, row in master.items()}


def project_records(records, master, target_date):
    """D1 after real point evidence; independent success is only local evidence.

    A later success does not erase a different question_ref's unresolved break.
    Source fragments remain the evidence authority; no associated node is graded.
    """
    day = dt.date.fromisoformat(target_date)
    memberships = point_memberships(master)
    by_point = {}
    evidence = []
    for record in records:
        if record.get("evidence_role") not in {"observed_learner_evidence", "learner_report"}:
            continue
        observed = dt.date.fromisoformat(record["observed_date"])
        if observed >= day:
            continue
        evidence.append(record)
        for point in record["knowledge_points"]:
            by_point.setdefault(point, []).append(record)
    requests = []
    for point, rows in sorted(by_point.items()):
        rows.sort(key=lambda r: (r.get("event_time") or r["observed_date"], r.get("source_order") or 0, r["source_fragment"]))
        pending = [r for r in rows if r["outcome"] in {"wrong", "uncertain", "correct", "partial", "relearn_required"}]
        if not pending:
            continue
        latest = rows[-1]
        last_gap = pending[-1]
        # A point-wide mastery claim is never inferred from one later correct item.
        rank = 0 if latest["outcome"] == "wrong" else (2 if latest["outcome"] == "independent_correct" else 1)
        reason = "wrong" if rank == 0 else ("local_success_other_evidence_pending" if rank == 2 else "verification_pending")
        due_date = (dt.date.fromisoformat(last_gap["observed_date"]) + dt.timedelta(days=1)).isoformat()
        request = {"concept_id": point, "priority_rank": rank, "reason": reason,
                   "request_due_date": due_date,
                   "candidate_formal_node_ids": [fid for fid, points in memberships.items() if point in points],
                   "evidence": [{k: r.get(k) for k in ("source_id", "source_sha256", "source_fragment",
                       "source_locator", "observed_date", "event_time", "outcome", "summary", "prompt_dependency")}
                       for r in rows],
                   "latest_outcome": latest["outcome"], "establishes_point_mastery": False,
                   "question_fsrs_write_count": 0}
        request["request_id"] = "DCR-" + digest(request)[:24]
        requests.append(request)
    requests.sort(key=lambda r: (r["priority_rank"], r["request_due_date"], r["concept_id"]))
    value = {"schema": POLICY, "target_date": target_date, "requests": requests,
             "evidence_sha256": digest(evidence), "formal_write_count": 0, "learner_evidence_write_count": 0}
    return {**value, "concept_priority_version": digest(value)}


def collect_priority_records(repo):
    from concept_review_408 import reopen, safe_records, session_paths
    records = []
    for path in session_paths(repo):
        records.extend(safe_records(reopen(path, repo)))
    from seal_knowledge_observations_408 import load_formal_records
    records.extend(load_formal_records(repo))
    fields = ("knowledge_points", "source_kind", "source_id", "source_sha256", "source_fragment", "source_locator",
              "observed_date", "event_time", "source_order", "outcome", "event_kind", "evidence_role",
              "summary", "prompt_dependency", "formal_ids", "semantic_annotation_sha256", "correction_result")
    return [{k: r.get(k) for k in fields} for r in records]


def load_priority(repo: Path, master, target_date):
    snapshot = Path(repo) / SNAPSHOT_REL
    if snapshot.exists():
        value = json.loads(snapshot.read_bytes())
        if value.get("schema") != "cs408-concept-evidence-snapshot-v1" or value.get("records_sha256") != digest(value.get("records")):
            raise ValueError("concept_evidence_snapshot_hash_mismatch")
        records = value["records"]
    else:
        records = collect_priority_records(repo)
    return project_records(records, master, target_date)


def due_context(run, master):
    groups = {}
    unmapped = []
    for item in run["due_items"]:
        fid = item["formal_node_id"]
        points = list(dict.fromkeys(re.findall(r"(?:DS|CO|OS|CN)\d{2}-\d{2}", str(master[fid].get("主知识点") or ""))))
        if len(points) != 1:
            unmapped.append(fid)
        for point in points:
            groups.setdefault(point, []).append(fid)
    priority = run.get("concept_review_priority", {})
    requests = priority.get("requests", [])
    memberships = point_memberships(master)
    due_ids = [row["formal_node_id"] for row in run["due_items"]]
    review_ids = list(dict.fromkeys([r["concept_id"] for r in requests] + list(groups)))
    return {"schema": "cs408-D-due-context-v1", "subject": "408", "project": "D",
        "target_date": run["run_date"], "due_version": run["run_sha256"],
        "concept_priority_version": priority.get("concept_priority_version"), "concept_requests": requests,
        "review_concept_ids": review_ids,
        "review_concepts": [{"concept_id": point, "formal_node_ids": groups.get(point, []),
            "due_candidate_formal_node_ids": [fid for fid in due_ids if point in memberships[fid]],
            "candidate_formal_node_ids": [fid for fid, points in memberships.items() if point in points],
            "request_ids": [r["request_id"] for r in requests if r["concept_id"] == point]} for point in review_ids],
        "due_concept_ids": sorted(groups),
        "due_concepts": [{"concept_id": point, "formal_node_ids": ids} for point, ids in sorted(groups.items())],
        "coverage": {"cohort_total": run["cohort_count"], "due_formal_node_ids": due_ids,
            "due_concept_ids": sorted(groups), "unmapped_due_formal_node_ids": unmapped,
            "blocked": run["blocked"], "authority_excluded": run["excluded"]},
        "native_due_run": run, "formal_write_count": 0, "learner_evidence_write_count": 0}
