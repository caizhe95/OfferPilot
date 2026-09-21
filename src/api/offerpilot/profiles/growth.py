"""Deterministic Profile growth summary reducer."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, now


def _profile_summary(conn: Any, profile_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT p.exam_point_id, p.status, p.evidence, r.id AS report_id, r.created_at "
        "FROM diagnosis_point_results p JOIN diagnosis_reports r ON r.id = p.report_id "
        "WHERE r.profile_id = ? ORDER BY r.created_at ASC",
        (profile_id,),
    ).fetchall()
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        observations[row["exam_point_id"]].append(
            {"status": row["status"], "evidence": row["evidence"], "created_at": row["created_at"]}
        )
    recurring = [
        point_id
        for point_id, items in observations.items()
        if sum(item["status"] in {"partial", "missing"} for item in items) >= 2
    ]
    mastered = [
        point_id
        for point_id, items in observations.items()
        if len(items) >= 2 and items[-1]["status"] == "covered" and items[-2]["status"] == "covered"
    ]
    return {
        "observed_points": len(observations),
        "recurring_weaknesses": recurring[:30],
        "mastered_topics": mastered[:30],
        "diagnosis_count": len({row["report_id"] for row in rows}),
    }


def rebuild_profile_growth(profile_id: str) -> dict[str, Any]:
    conn = get_db()
    try:
        summary = _profile_summary(conn, profile_id)
        row = conn.execute(
            "SELECT summary_version FROM profile_growth_summaries WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        version = int(row["summary_version"] if row else 0) + 1
        timestamp = now()
        conn.execute(
            "INSERT INTO profile_growth_summaries(profile_id, summary_version, summary_json, updated_at) "
            "VALUES(?, ?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET "
            "summary_version=excluded.summary_version, summary_json=excluded.summary_json, updated_at=excluded.updated_at",
            (profile_id, version, dumps(summary), timestamp),
        )
        conn.commit()
        return {"profile_id": profile_id, "summary_version": version, "summary_json": summary, "updated_at": timestamp}
    finally:
        conn.close()


def get_profile_growth(profile_id: str) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT profile_id, summary_version, summary_json, updated_at "
            "FROM profile_growth_summaries WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row:
            return {
                "profile_id": row["profile_id"],
                "summary_version": row["summary_version"],
                "summary_json": json.loads(row["summary_json"] or "{}"),
                "updated_at": row["updated_at"],
            }
    finally:
        conn.close()
    return rebuild_profile_growth(profile_id)
