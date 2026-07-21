"""Session API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.core.profile import require_owned_session, require_profile_id
from app.session.session import (
    create_session,
    get_session,
    transition_session,
    add_message,
    get_recent_messages,
    add_progress_event,
    get_progress_events,
    save_checkpoint,
    get_checkpoint,
    get_latest_checkpoint,
    list_sessions,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    metadata: dict | None = None


class AddMessageRequest(BaseModel):
    role: str
    content: str


class TransitionRequest(BaseModel):
    status: str


class ProgressEventRequest(BaseModel):
    stage: str
    metadata: dict | None = None


class CheckpointRequest(BaseModel):
    state: str
    progress: list[str] | None = None
    messages: list[dict] | None = None
    knowledge: list[str] | None = None
    memory_keys: list[str] | None = None
    trace_id: str = ""
    run_kind: str = ""


@router.post("")
async def create_session_endpoint(
    request: CreateSessionRequest = CreateSessionRequest(),
    profile_id: str = Depends(require_profile_id),
):
    """Create a new session."""
    return create_session(profile_id, request.metadata)


@router.get("")
async def list_sessions_endpoint(
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    profile_id: str = Depends(require_profile_id),
):
    """List sessions."""
    return {"sessions": list_sessions(profile_id, status=status, limit=limit)}


@router.get("/{session_id}")
async def get_session_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    """Get session by ID."""
    session = get_session(session_id)
    return require_owned_session(session, profile_id)


@router.post("/{session_id}/transition")
async def transition_session_endpoint(session_id: str, request: TransitionRequest, profile_id: str = Depends(require_profile_id)):
    """Transition session to a new status."""
    try:
        require_owned_session(get_session(session_id), profile_id)
        session = transition_session(session_id, request.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/messages")
async def add_message_endpoint(session_id: str, request: AddMessageRequest, profile_id: str = Depends(require_profile_id)):
    """Add a message to a session."""
    require_owned_session(get_session(session_id), profile_id)
    return add_message(session_id, request.role, request.content)


@router.get("/{session_id}/messages")
async def get_messages_endpoint(
    session_id: str,
    n: int = Query(10, ge=1, le=100, description="Number of recent messages"),
    profile_id: str = Depends(require_profile_id),
):
    """Get recent messages for a session."""
    require_owned_session(get_session(session_id), profile_id)
    return {"messages": get_recent_messages(session_id, n=n)}


@router.post("/{session_id}/progress")
async def add_progress_endpoint(session_id: str, request: ProgressEventRequest, profile_id: str = Depends(require_profile_id)):
    """Add a progress event."""
    try:
        require_owned_session(get_session(session_id), profile_id)
        return add_progress_event(session_id, request.stage, request.metadata)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/progress")
async def get_progress_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    """Get all progress events for a session."""
    require_owned_session(get_session(session_id), profile_id)
    return {"events": get_progress_events(session_id)}


@router.post("/{session_id}/checkpoints")
async def save_checkpoint_endpoint(session_id: str, request: CheckpointRequest, profile_id: str = Depends(require_profile_id)):
    """Save a checkpoint."""
    require_owned_session(get_session(session_id), profile_id)
    return save_checkpoint(
        session_id,
        request.state,
        request.progress,
        request.messages,
        request.knowledge,
        request.memory_keys,
        trace_id=request.trace_id,
        run_kind=request.run_kind,
    )


@router.get("/{session_id}/checkpoints/latest")
async def get_latest_checkpoint_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    """Get latest checkpoint for a session."""
    require_owned_session(get_session(session_id), profile_id)
    checkpoint = get_latest_checkpoint(session_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="No checkpoint found")
    return checkpoint


@router.get("/{session_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint_endpoint(session_id: str, checkpoint_id: str, profile_id: str = Depends(require_profile_id)):
    """Get a specific checkpoint."""
    require_owned_session(get_session(session_id), profile_id)
    checkpoint = get_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    if checkpoint["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return checkpoint
