"""대화 기록 API: 저장, 목록(메시지 제외), 불러오기(전체 메시지), 삭제."""

from fastapi import APIRouter, Depends, Query, Response, status

from deps import get_store
from models import ConversationCreate, ConversationDetail, ConversationMeta

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("", response_model=ConversationMeta, status_code=status.HTTP_201_CREATED)
def create_conversation(req: ConversationCreate, store=Depends(get_store)):
    title = req.title or req.messages[0].content[:30]
    conversation_id = store.create_conversation(title, req.messages)
    return store.get_conversation(conversation_id)


@router.get("", response_model=list[ConversationMeta])
def list_conversations(limit: int = Query(50, ge=1, le=100), store=Depends(get_store)):
    """대화 목록. messages는 포함하지 않는다 (불러오기는 GET /api/conversations/{id})."""
    return store.list_conversations(limit)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, store=Depends(get_store)):
    return store.get_conversation(conversation_id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, store=Depends(get_store)):
    store.delete_conversation(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
