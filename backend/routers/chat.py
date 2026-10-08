"""채팅 API: 컨텍스트 주입 흐름은 ChatService가 맡고, 여기서는 HTTP 변환만 한다."""

from fastapi import APIRouter, Depends

from deps import get_chat_service
from models import ChatRequest, ChatResponse
from service import ChatService

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, service: ChatService = Depends(get_chat_service)):
    """요약 조회 → 시스템 프롬프트 주입 → GPT 호출 → 대화 자동 저장."""
    conversation_id, reply = service.chat(req.message, req.conversation_id)
    return ChatResponse(conversation_id=conversation_id, reply=reply)
