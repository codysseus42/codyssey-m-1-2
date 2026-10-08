"""의존성 주입. 라우터는 여기서 저장소·AI 클라이언트를 받아 쓴다.

main.py가 아니라 별도 파일에 두는 이유:
- main.py는 routers를 import하므로, routers가 다시 main을 import하면 순환 import가 된다.
- 테스트는 app.dependency_overrides[deps.get_store]처럼 이 함수 객체를 키로 가짜를 끼운다.
"""

from functools import lru_cache

from fastapi import Depends

from service import ChatService, OpenAIChatClient
from store import FirestoreStore, create_firestore_client


@lru_cache
def get_store() -> FirestoreStore:
    return FirestoreStore(create_firestore_client())


@lru_cache
def get_llm() -> OpenAIChatClient:
    return OpenAIChatClient()


def get_chat_service(store=Depends(get_store), llm=Depends(get_llm)) -> ChatService:
    return ChatService(store, llm)
