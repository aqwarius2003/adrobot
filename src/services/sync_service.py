import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.core.logging import logger
from src.domain.models import Campaign, Operation, Stream, StreamOffer
from src.domain.schemas import StreamDTO
from src.domain.weights import compute_stream_fingerprint
from src.services.editor_service import (
    EditorConflictError,
    EditorValidationError,
    StreamEditorService,
)
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway

WORKER_ID = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class SyncConflictError(Exception):
    """Raised when remote stream differs from local Baseline or revision conflict occurs."""

    pass


class SyncVerificationError(Exception):
    """Raised when read-back verification after PUT indicates state mismatch."""

    pass


class StreamSyncService:
    """Service handling Push to Keitaro, preflight, read-back, and reconciliation."""

    def __init__(self, db: Session, kt_client: KeitaroGateway):
        self.db = db
        self.kt_client = kt_client
        self.editor_service = StreamEditorService(db, kt_client)

    async def push_stream(
        self,
        stream_id: int,
        expected_revision: int,
        idempotency_key: str | None = None,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise ValueError("Managed stream not found")

        if expected_remote_campaign_id is not None:
            campaign = self.db.query(Campaign).filter_by(id=stream.campaign_id).first()
            if not campaign or campaign.remote_campaign_id != expected_remote_campaign_id:
                raise SyncConflictError(
                    f"Stream #{stream.id} does not belong to expected remote campaign #{expected_remote_campaign_id}"
                )

        key = idempotency_key or str(uuid.uuid4())
        now_ts = time.time()

        # Idempotency check: replay previous result before stream revision checks to prevent duplicate submissions
        # If an identical request was already completed, return confirmed DTO immediately without second PUT
        if idempotency_key:
            existing_op = (
                self.db.query(Operation).filter_by(idempotency_key=idempotency_key).first()
            )
            if existing_op:
                if (
                    existing_op.operation_type != "push_stream"
                    or existing_op.target_id != stream.id
                ):
                    raise EditorValidationError(
                        f"Idempotency key '{idempotency_key}' was used for a different operation or stream"
                    )
                if existing_op.status == "success":
                    return self.editor_service._build_stream_dto(stream)

        # F5: Block push if already in publishing/verifying/unknown
        if stream.sync_status in ("publishing", "verifying"):
            raise SyncConflictError(
                f"Stream is currently being pushed (state='{stream.sync_status}'). Concurrent push locked."
            )

        if stream.sync_status == "unknown":
            # Attempt recovery/reconciliation before allowing new push
            return await self.reconcile_stream(stream.id)

        if stream.revision != expected_revision:
            raise SyncConflictError(
                f"Stream revision changed (expected {expected_revision}, current {stream.revision}). Reload required."
            )

        active_offers = (
            self.db.query(StreamOffer)
            .filter_by(stream_id=stream.id, draft_state="active")
            .order_by(StreamOffer.position)
            .all()
        )
        if not active_offers:
            raise ValueError("Cannot push stream with no active offers")

        total_share = sum(so.draft_share for so in active_offers)
        if total_share != 100:
            raise ValueError(f"Active offers share must equal 100% (currently {total_share}%)")

        # Separate KT payload (no pins) from full immutable local snapshot (with pins and base_revision)
        candidate_offers = [
            {
                "offer_id": so.offer.remote_offer_id,
                "share": so.draft_share,
                "state": "active",
            }
            for so in active_offers
        ]
        kt_payload = {"offers": candidate_offers}

        local_snapshot = [
            {
                "remote_offer_id": so.offer.remote_offer_id,
                "share": so.draft_share,
                "state": so.draft_state,
                "is_pinned": so.draft_pinned,
            }
            for so in stream.stream_offers
        ]

        stored_payload = {
            "kt_payload": kt_payload,
            "local_snapshot": local_snapshot,
            "base_revision": expected_revision,
            "offers": candidate_offers,  # For backward compatibility
        }

        # Check for concurrent active push lease by another worker
        active_conflict_op = (
            self.db.query(Operation)
            .filter_by(target_id=stream.id, operation_type="push_stream")
            .filter(Operation.idempotency_key != key)
            .filter(Operation.status.in_(["in_progress", "publishing"]))
            .first()
        )
        if (
            active_conflict_op
            and active_conflict_op.lease_expires_at
            and active_conflict_op.lease_expires_at > now_ts
        ):
            raise SyncConflictError(
                f"Stream #{stream_id} is currently being published by worker '{active_conflict_op.worker_id}'."
            )

        op = self.db.query(Operation).filter_by(idempotency_key=key).first()
        if op:
            if op.operation_type != "push_stream" or op.target_id != stream.id:
                raise EditorValidationError(
                    f"Idempotency key '{key}' was already used for a different operation or stream"
                )
            if op.candidate_payload != stored_payload and op.candidate_payload != kt_payload:
                raise EditorValidationError(
                    f"Idempotency key '{key}' was already used with a different candidate payload"
                )
            if op.status == "success":
                return self.editor_service._build_stream_dto(stream)
            op.worker_id = WORKER_ID
            op.lease_expires_at = now_ts + 30.0
            self.db.commit()

        # Atomic stream claim: guarantees single-winner concurrency and prevents race conditions across independent workers
        claimed_rows = (
            self.db.query(Stream)
            .filter(
                Stream.id == stream.id,
                Stream.revision == expected_revision,
                Stream.sync_status.notin_(["publishing", "verifying", "unknown"]),
            )
            .update({"sync_status": "publishing"}, synchronize_session="fetch")
        )
        if claimed_rows == 0:
            self.db.rollback()
            raise SyncConflictError(
                f"Stream #{stream.id} could not be claimed: concurrent operation or revision changed."
            )

        if not op:
            op = Operation(
                idempotency_key=key,
                operation_type="push_stream",
                target_id=stream.id,
                stage="preflight",
                status="in_progress",
                candidate_payload=stored_payload,
                worker_id=WORKER_ID,
                lease_expires_at=now_ts + 30.0,
            )
            self.db.add(op)
        self.db.commit()

        try:
            remote_stream = await self.kt_client.get_stream(stream.remote_stream_id)
        except Exception as exc:
            stream.sync_status = "dirty"
            op.status = "failed"
            op.error_message = f"Preflight fetch failed: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Preflight error contacting Keitaro: {exc}") from exc

        current_remote_fp = compute_stream_fingerprint(remote_stream)
        if stream.remote_fingerprint and current_remote_fp != stream.remote_fingerprint:
            stream.sync_status = "conflict"
            op.status = "failed"
            op.error_message = (
                "Remote stream was modified in Keitaro. Preflight fingerprint conflict."
            )
            self.db.commit()
            raise SyncConflictError(
                "Conflict: Remote stream was modified in Keitaro since last fetch. Resolve via Cancel -> Fetch Streams."
            )

        # Static optimization: Pin-only change check (if remote offers match candidate, skip PUT)
        remote_offers_dict = {
            o.get("offer_id"): o.get("share")
            for o in (remote_stream.get("offers") or [])
            if o.get("state", "active") == "active"
        }
        cand_dict = {o["offer_id"]: o["share"] for o in candidate_offers}

        if remote_offers_dict == cand_dict:
            # KT already has identical offers; only local pins or flags changed!
            logger.info("Pin-only or no-op push detected: skipping remote PUT to Keitaro")
            for so in stream.stream_offers:
                so.draft_auto_pinned = False
                if so.draft_state == "active":
                    so.baseline_share = so.draft_share
                    so.baseline_state = "active"
                    so.baseline_pinned = so.draft_pinned
                    so.has_been_published = True
                else:
                    so.baseline_share = 0
                    so.baseline_state = "removed"
                    so.baseline_pinned = False

            stream.sync_status = "clean"
            stream.remote_fingerprint = current_remote_fp
            stream.last_sync_at = datetime.now(UTC)
            stream.revision += 1
            op.status = "success"
            op.stage = "completed"
            self.db.commit()

            return self.editor_service._build_stream_dto(stream)

        # Send updated stream offer shares to Keitaro via PUT
        op.stage = "updating_kt"
        op.lease_expires_at = time.time() + 30.0
        self.db.commit()

        try:
            await self.kt_client.update_stream(stream.remote_stream_id, kt_payload)
        except KeitaroApiError as exc:
            # If server explicitly answered with client/validation error, write was definitely rejected
            if exc.status_code in (400, 401, 403, 404, 409, 422):
                stream.sync_status = "dirty"
                op.status = "failed"
                op.error_message = f"Keitaro PUT update rejected: {exc}"
                self.db.commit()
                raise
            # If timeout or network error during PUT, write status is uncertain
            stream.sync_status = "unknown"
            op.status = "unknown"
            op.error_message = f"Connection or timeout during stream update: {exc}"
            self.db.commit()
            return await self._reconcile_timeout(stream, candidate_offers, op)

        # Read-back verification: fetch stream state from Keitaro to confirm applied changes
        op.stage = "verifying"
        op.lease_expires_at = time.time() + 30.0
        self.db.commit()

        try:
            read_back_stream = await self.kt_client.get_stream(stream.remote_stream_id)
        except Exception as exc:
            stream.sync_status = "unknown"
            op.status = "unknown"
            op.error_message = f"Read-back GET failed: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Read-back verification GET failed: {exc}") from exc

        rb_offers = read_back_stream.get("offers") or []
        rb_dict = {
            o.get("offer_id"): o.get("share") for o in rb_offers if o.get("state") == "active"
        }

        if rb_dict != cand_dict:
            stream.sync_status = "conflict"
            op.status = "failed"
            op.error_message = f"Read-back mismatch: expected {cand_dict}, got {rb_dict}"
            self.db.commit()
            raise SyncVerificationError(
                f"Read-back verification mismatch between Keitaro and published candidate. Expected {cand_dict}, got {rb_dict}"
            )

        # Verify worker ownership and update Baseline snapshot atomically
        self.db.expire_all()
        current_op = self.db.query(Operation).filter_by(id=op.id).first()
        if not current_op or current_op.worker_id != WORKER_ID:
            raise SyncConflictError(
                f"Push operation #{op.id} ownership was lost: claimed by another worker."
            )
        if current_op.status not in ("in_progress", "publishing", "verifying"):
            raise SyncConflictError(
                f"Push operation #{op.id} was already completed or superseded (status={current_op.status})."
            )

        for so in stream.stream_offers:
            so.draft_auto_pinned = False
            if so.draft_state == "active":
                so.baseline_share = so.draft_share
                so.baseline_state = "active"
                so.baseline_pinned = so.draft_pinned
                so.has_been_published = True
                so.archive_reason = None
            else:
                so.baseline_share = 0
                so.baseline_state = "removed"
                so.baseline_pinned = False

        is_clean = True
        for so in stream.stream_offers:
            if (
                so.draft_share != so.baseline_share
                or so.draft_state != so.baseline_state
                or so.draft_pinned != so.baseline_pinned
            ):
                is_clean = False
                break

        stream.sync_status = "clean" if is_clean else "dirty"
        stream.remote_fingerprint = compute_stream_fingerprint(read_back_stream)
        stream.last_sync_at = datetime.now(UTC)
        stream.revision += 1

        op.stage = "completed"
        op.status = "success"
        self.db.commit()

        return self.editor_service._build_stream_dto(stream)

    async def reconcile_stream(
        self, stream_id: int, expected_campaign_id: int | None = None
    ) -> StreamDTO:
        """Perform reconciliation for a stream with undetermined status (unknown or verifying)."""
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream:
            raise ValueError(f"Stream #{stream_id} not found")

        # Disallow reconciliation on normal dirty or clean streams without an undetermined operation
        if stream.sync_status not in ("unknown", "verifying"):
            raise EditorConflictError(
                f"Reconciliation is only permitted for streams with undetermined sync state ('unknown' or 'verifying'), but stream '{stream.name}' is currently '{stream.sync_status}'."
            )

        if expected_campaign_id is not None:
            camp = self.db.query(Campaign).filter_by(id=stream.campaign_id).first()
            if camp and camp.remote_campaign_id != expected_campaign_id:
                raise EditorValidationError(
                    f"Stream #{stream_id} does not belong to campaign #{expected_campaign_id}"
                )

        last_op = (
            self.db.query(Operation)
            .filter_by(target_id=stream.id, operation_type="push_stream")
            .filter(
                Operation.status.in_(
                    ["unknown", "in_flight", "in_progress", "timeout", "publishing"]
                )
            )
            .order_by(Operation.id.desc())
            .first()
        )
        if not last_op or not last_op.candidate_payload:
            raise EditorConflictError(
                f"No undetermined push operation found for stream '{stream.name}' to reconcile."
            )

        candidate_offers = last_op.candidate_payload.get("offers") or []
        if not candidate_offers:
            raise EditorConflictError(
                f"Operation #{last_op.id} candidate payload has no offers to reconcile."
            )

        return await self._reconcile_timeout(stream, candidate_offers, last_op)

    async def _reconcile_timeout(
        self, stream: Stream, candidate_offers: list[dict[str, Any]], op: Operation | None
    ) -> StreamDTO:
        try:
            read_back = await self.kt_client.get_stream(stream.remote_stream_id)
            rb_offers = read_back.get("offers") or []
            rb_dict = {
                o.get("offer_id"): o.get("share")
                for o in rb_offers
                if o.get("state", "active") == "active"
            }
            cand_dict = {o["offer_id"]: o["share"] for o in candidate_offers}

            baseline_offers = [so for so in stream.stream_offers if so.baseline_state == "active"]
            baseline_dict = {so.offer.remote_offer_id: so.baseline_share for so in baseline_offers}

            self.db.expire_all()

            # Atomic verification of operation currency and generation
            if op:
                base_rev = None
                if isinstance(op.candidate_payload, dict):
                    base_rev = op.candidate_payload.get("base_revision")

                # If another operation already completed and bumped stream revision, this reconcile is stale!
                if base_rev is not None and stream.revision > base_rev:
                    logger.warning(
                        "Stream %s: operation #%s base revision (%s) is superseded by stream revision (%s). Abandoning stale reconcile.",
                        stream.id,
                        op.id,
                        base_rev,
                        stream.revision,
                    )
                    op.status = "superseded"
                    self.db.commit()
                    return self.editor_service._build_stream_dto(stream)

            if cand_dict and rb_dict == cand_dict:
                # Outcome 1: KT state matches candidate -> confirmed success of candidate
                # Update Baseline strictly from candidate snapshot, preserving pins from local snapshot
                cand_offer_map = {o["offer_id"]: o for o in candidate_offers}
                local_snap_map = {}
                if (
                    op
                    and isinstance(op.candidate_payload, dict)
                    and "local_snapshot" in op.candidate_payload
                ):
                    local_snap_map = {
                        item["remote_offer_id"]: item
                        for item in op.candidate_payload["local_snapshot"]
                    }

                for so in stream.stream_offers:
                    remote_off_id = so.offer.remote_offer_id
                    if remote_off_id in cand_offer_map:
                        c_item = cand_offer_map[remote_off_id]
                        so.baseline_share = c_item["share"]
                        so.baseline_state = c_item.get("state", "active")
                        if remote_off_id in local_snap_map:
                            so.baseline_pinned = local_snap_map[remote_off_id].get(
                                "is_pinned", False
                            )
                        else:
                            so.baseline_pinned = c_item.get("is_pinned", False)
                        so.has_been_published = True
                        so.archive_reason = None
                    else:
                        so.baseline_share = 0
                        so.baseline_state = "removed"
                        so.baseline_pinned = False

                # Strictly evaluate clean vs dirty including pins!
                is_clean = True
                for so in stream.stream_offers:
                    if (
                        so.draft_share == so.baseline_share
                        and so.draft_state == so.baseline_state
                        and so.draft_pinned == so.baseline_pinned
                    ):
                        so.draft_auto_pinned = False
                    if (
                        so.draft_share != so.baseline_share
                        or so.draft_state != so.baseline_state
                        or so.draft_pinned != so.baseline_pinned
                    ):
                        is_clean = False
                        break

                stream.sync_status = "clean" if is_clean else "dirty"
                stream.remote_fingerprint = compute_stream_fingerprint(read_back)
                stream.last_sync_at = datetime.now(UTC)
                stream.revision += 1
                if op:
                    op.status = "success"
                    op.stage = "completed"
                self.db.commit()
                logger.info(
                    "Timeout reconciled successfully: KT state matches candidate (clean=%s)",
                    is_clean,
                )
            elif rb_dict == baseline_dict:
                # Outcome 2: KT state matches old baseline -> write unapplied/failed before KT, draft preserved
                stream.sync_status = "dirty"
                if op:
                    op.status = "failed"
                    op.error_message = (
                        "Remote matches baseline: write was unapplied, safe retry allowed."
                    )
                self.db.commit()
                logger.info(
                    "Timeout reconciled: remote matches baseline, draft preserved with dirty status"
                )
            else:
                # Outcome 3: KT state matches neither -> true external conflict
                stream.sync_status = "conflict"
                if op:
                    op.status = "failed"
                    op.error_message = f"Timeout reconciliation conflict: remote {rb_dict} vs cand {cand_dict} vs base {baseline_dict}"
                self.db.commit()
                logger.warning(
                    "Timeout reconciliation found discrepancy: %s vs %s vs %s",
                    rb_dict,
                    cand_dict,
                    baseline_dict,
                )
        except Exception as exc:
            stream.sync_status = "unknown"
            if op:
                op.status = "unknown"
                op.error_message = f"Failed to perform reconciliation read-back: {exc}"
            self.db.commit()
            logger.error("Failed to perform reconciliation: %s", exc)

        return self.editor_service._build_stream_dto(stream)
