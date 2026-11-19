import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.core.logging import logger
from src.domain.models import Operation, Stream
from src.domain.schemas import StreamDTO
from src.services.editor_service import StreamEditorService
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway


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

    def _compute_fingerprint(self, stream_data: dict[str, Any]) -> str:
        offers = stream_data.get("offers") or []
        normalized_offers = sorted(
            [
                {
                    "offer_id": o.get("offer_id"),
                    "share": o.get("share"),
                    "state": o.get("state", "active"),
                }
                for o in offers
            ],
            key=lambda x: x["offer_id"] or 0,
        )
        content = {
            "type": stream_data.get("type"),
            "schema": stream_data.get("schema"),
            "action_type": stream_data.get("action_type"),
            "action_payload": stream_data.get("action_payload"),
            "offers": normalized_offers,
        }
        encoded = json.dumps(content, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def push_stream(
        self,
        stream_id: int,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise ValueError("Managed stream not found")

        if stream.revision != expected_revision:
            raise SyncConflictError(
                f"Stream revision changed (expected {expected_revision}, current {stream.revision}). Reload required."
            )

        active_offers = [so for so in stream.stream_offers if so.draft_state == "active"]
        if not active_offers:
            raise ValueError("Cannot push stream with no active offers")

        total_share = sum(so.draft_share for so in active_offers)
        if total_share != 100:
            raise ValueError(f"Active offers share must equal 100% (currently {total_share}%)")

        key = idempotency_key or str(uuid.uuid4())
        op = self.db.query(Operation).filter_by(idempotency_key=key).first()
        if op and op.status == "success":
            detail = self.editor_service.get_campaign_detail(stream.campaign_id)
            return next(s for s in detail.streams if s.id == stream.id)

        if not op:
            op = Operation(
                idempotency_key=key,
                operation_type="push_stream",
                target_id=stream.id,
                stage="preflight",
                status="in_progress",
            )
            self.db.add(op)
            self.db.commit()

        # Step 1: Preflight check - fetch remote state and compare with baseline fingerprint
        stream.sync_status = "publishing"
        self.db.commit()

        try:
            remote_stream = await self.kt_client.get_stream(stream.remote_stream_id)
        except Exception as exc:
            stream.sync_status = "dirty"
            op.status = "failed"
            op.error_message = f"Preflight fetch failed: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Preflight error contacting Keitaro: {exc}") from exc

        current_remote_fp = self._compute_fingerprint(remote_stream)
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

        # Step 2: Prepare payload (ONLY active offers, exact integer shares, no pins, no removed)
        candidate_offers = [
            {
                "offer_id": so.offer.remote_offer_id,
                "share": so.draft_share,
                "state": "active",
            }
            for so in active_offers
        ]
        candidate_payload = {"offers": candidate_offers}
        op.candidate_payload = candidate_payload
        op.stage = "updating_kt"
        self.db.commit()

        # Step 3: Send PUT to Keitaro
        try:
            await self.kt_client.update_stream(stream.remote_stream_id, candidate_payload)
        except KeitaroApiError as exc:
            if exc.status_code == 504:
                # Timeout after request sent: mark unknown and attempt reconciliation
                stream.sync_status = "unknown"
                op.status = "unknown"
                op.error_message = f"Timeout during stream update: {exc}"
                self.db.commit()
                # Attempt reconciliation GET
                return await self._reconcile_timeout(stream, candidate_offers, op)
            stream.sync_status = "dirty"
            op.status = "failed"
            op.error_message = f"Keitaro PUT update failed: {exc}"
            self.db.commit()
            raise

        # Step 4: Read-back Verification GET
        op.stage = "verifying"
        self.db.commit()

        try:
            read_back_stream = await self.kt_client.get_stream(stream.remote_stream_id)
        except Exception as exc:
            stream.sync_status = "unknown"
            op.status = "unknown"
            op.error_message = f"Read-back GET failed: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Read-back verification GET failed: {exc}") from exc

        # Compare read-back offers
        rb_offers = read_back_stream.get("offers") or []
        rb_dict = {
            o.get("offer_id"): o.get("share") for o in rb_offers if o.get("state") == "active"
        }
        cand_dict = {o["offer_id"]: o["share"] for o in candidate_offers}

        if rb_dict != cand_dict:
            stream.sync_status = "conflict"
            op.status = "failed"
            op.error_message = f"Read-back mismatch: expected {cand_dict}, got {rb_dict}"
            self.db.commit()
            raise SyncVerificationError(
                f"Read-back verification mismatch between Keitaro and published candidate. Expected {cand_dict}, got {rb_dict}"
            )

        # Step 5: Update Baseline atomically
        for so in stream.stream_offers:
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

        stream.sync_status = "clean"
        stream.remote_fingerprint = self._compute_fingerprint(read_back_stream)
        stream.last_sync_at = datetime.now(UTC)
        stream.revision += 1

        op.stage = "completed"
        op.status = "success"
        self.db.commit()

        detail = self.editor_service.get_campaign_detail(stream.campaign_id)
        return next(s for s in detail.streams if s.id == stream.id)

    async def _reconcile_timeout(
        self, stream: Stream, candidate_offers: list[dict[str, Any]], op: Operation
    ) -> StreamDTO:
        """Reconcile state after a 504 timeout on PUT."""
        try:
            read_back = await self.kt_client.get_stream(stream.remote_stream_id)
            rb_offers = read_back.get("offers") or []
            rb_dict = {
                o.get("offer_id"): o.get("share") for o in rb_offers if o.get("state") == "active"
            }
            cand_dict = {o["offer_id"]: o["share"] for o in candidate_offers}

            if rb_dict == cand_dict:
                # Upstream accepted the write before timing out!
                for so in stream.stream_offers:
                    if so.draft_state == "active":
                        so.baseline_share = so.draft_share
                        so.baseline_state = "active"
                        so.baseline_pinned = so.draft_pinned
                        so.has_been_published = True
                    else:
                        so.baseline_share = 0
                        so.baseline_state = "removed"

                stream.sync_status = "clean"
                stream.remote_fingerprint = self._compute_fingerprint(read_back)
                stream.last_sync_at = datetime.now(UTC)
                stream.revision += 1
                op.status = "success"
                op.stage = "completed"
                self.db.commit()
                logger.info("Timeout reconciled successfully: KT state matches candidate")
            else:
                logger.warning(
                    "Timeout reconciliation found discrepancy: %s vs %s", rb_dict, cand_dict
                )
        except Exception as exc:
            logger.error("Failed to perform reconciliation: %s", exc)

        detail = self.editor_service.get_campaign_detail(stream.campaign_id)
        return next(s for s in detail.streams if s.id == stream.id)
