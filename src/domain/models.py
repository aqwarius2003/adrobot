from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    remote_campaign_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    streams: Mapped[list["Stream"]] = relationship(
        "Stream", back_populates="campaign", cascade="all, delete-orphan"
    )


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    remote_stream_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=1)
    is_managed: Mapped[bool] = mapped_column(
        Boolean, default=True
    )  # True = offers stream, False = opaque stream
    stream_type: Mapped[str] = mapped_column(String(64), default="regular")
    stream_schema: Mapped[str] = mapped_column(String(64), default="landings")
    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_payload: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    sync_status: Mapped[str] = mapped_column(
        String(64), default="clean"
    )  # clean, dirty, publishing, verifying, conflict, unknown, remote_missing
    remote_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_remote_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="streams")
    stream_offers: Mapped[list["StreamOffer"]] = relationship(
        "StreamOffer",
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="StreamOffer.position",
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "remote_stream_id", name="uq_campaign_remote_stream"),
    )


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    remote_offer_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    stream_offers: Mapped[list["StreamOffer"]] = relationship("StreamOffer", back_populates="offer")


class StreamOffer(Base):
    __tablename__ = "stream_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("streams.id", ondelete="CASCADE"), index=True
    )
    offer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("offers.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    # Baseline: confirmed remote KT state + persisted pin flag
    baseline_share: Mapped[int] = mapped_column(Integer, default=0)
    baseline_state: Mapped[str] = mapped_column(String(32), default="active")  # active, removed
    baseline_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    # Draft: local uncommitted changes
    draft_share: Mapped[int] = mapped_column(Integer, default=0)
    draft_state: Mapped[str] = mapped_column(String(32), default="active")  # active, removed
    draft_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    archive_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # local_remove, remote_missing
    has_been_published: Mapped[bool] = mapped_column(Boolean, default=False)

    stream: Mapped["Stream"] = relationship("Stream", back_populates="stream_offers")
    offer: Mapped["Offer"] = relationship("Offer", back_populates="stream_offers")

    __table_args__ = (UniqueConstraint("stream_id", "offer_id", name="uq_stream_offer"),)


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    operation_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # create_campaign, push_stream, fetch_streams
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[str] = mapped_column(String(64), default="pending")
    status: Mapped[str] = mapped_column(
        String(64), default="pending"
    )  # pending, in_progress, success, failed, unknown
    candidate_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    remote_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
