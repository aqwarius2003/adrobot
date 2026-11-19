from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_editor_service, get_sync_service
from src.domain.models import Operation
from src.domain.schemas import (
    AddOfferRequest,
    OfferMutationRequest,
    OperationStatusResponse,
    PushRequest,
    StreamDTO,
    UpdateOfferWeightRequest,
)
from src.services.editor_service import (
    EditorConflictError,
    EditorValidationError,
    StreamEditorService,
)
from src.services.keitaro_client import KeitaroApiError
from src.services.sync_service import StreamSyncService, SyncConflictError, SyncVerificationError

router = APIRouter(prefix="/api", tags=["Streams"])


@router.post("/streams/{id}/offers", response_model=StreamDTO)
async def add_offer_to_stream(
    id: int,
    request: AddOfferRequest,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> StreamDTO:
    try:
        return await editor_service.add_offer(
            stream_id=id,
            offer_id=request.offer_id,
            expected_revision=request.expected_revision,
        )
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EditorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/streams/{id}/offers/{offer_id}/remove", response_model=StreamDTO)
async def remove_offer_from_stream(
    id: int,
    offer_id: int,
    request: OfferMutationRequest,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> StreamDTO:
    try:
        return await editor_service.remove_offer(
            stream_id=id,
            offer_id=offer_id,
            expected_revision=request.expected_revision,
        )
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EditorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/streams/{id}/offers/{offer_id}/bring-back", response_model=StreamDTO)
async def bring_back_offer_to_stream(
    id: int,
    offer_id: int,
    request: OfferMutationRequest,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> StreamDTO:
    try:
        return await editor_service.bring_back_offer(
            stream_id=id,
            offer_id=offer_id,
            expected_revision=request.expected_revision,
        )
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EditorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.patch("/streams/{id}/offers/{offer_id}", response_model=StreamDTO)
async def update_offer_weight(
    id: int,
    offer_id: int,
    request: UpdateOfferWeightRequest,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> StreamDTO:
    try:
        return await editor_service.update_offer_weight(
            stream_id=id,
            offer_id=offer_id,
            share=request.share,
            is_pinned=request.is_pinned,
            expected_revision=request.expected_revision,
        )
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EditorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/streams/{id}/cancel", response_model=StreamDTO)
def cancel_stream_changes(
    id: int,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> StreamDTO:
    try:
        return editor_service.cancel_stream(stream_id=id)
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EditorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/streams/{id}/push", response_model=StreamDTO)
async def push_stream_to_keitaro(
    id: int,
    request: PushRequest,
    sync_service: StreamSyncService = Depends(get_sync_service),
) -> StreamDTO:
    try:
        return await sync_service.push_stream(
            stream_id=id,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
        )
    except SyncConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SyncVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except KeitaroApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/operations/{id}", response_model=OperationStatusResponse)
def get_operation_status(
    id: int,
    db: Session = Depends(get_db),
) -> OperationStatusResponse:
    op = db.query(Operation).filter_by(id=id).first()
    if not op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    return OperationStatusResponse(
        id=op.id,
        idempotency_key=op.idempotency_key,
        operation_type=op.operation_type,
        status=op.status,
        stage=op.stage,
        error_message=op.error_message,
        remote_ids=op.remote_ids,
    )
