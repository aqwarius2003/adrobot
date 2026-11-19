from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.services.creator_service import CampaignCreatorService
from src.services.editor_service import StreamEditorService
from src.services.keitaro_client import KeitaroClient, KeitaroGateway
from src.services.sync_service import StreamSyncService


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_keitaro_client() -> KeitaroGateway:
    return KeitaroClient()


def get_creator_service(
    db: Session = Depends(get_db),
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> CampaignCreatorService:
    return CampaignCreatorService(db, kt_client)


def get_editor_service(
    db: Session = Depends(get_db),
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> StreamEditorService:
    return StreamEditorService(db, kt_client)


def get_sync_service(
    db: Session = Depends(get_db),
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> StreamSyncService:
    return StreamSyncService(db, kt_client)
