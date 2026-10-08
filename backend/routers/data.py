"""데이터 API: 월별 불쾌지수 CRUD와 요약."""

from fastapi import APIRouter, Depends, Response, status

from deps import get_store
from errors import NotFoundError
from models import DataCreate, DataItem, DataUpdate, Summary
from service import compute_summary

router = APIRouter(prefix="/api/data", tags=["data"])


# /summary를 /{data_id}보다 먼저 선언한다. (GET /{data_id}는 없지만 순서 습관으로 유지)
@router.get("/summary", response_model=Summary)
def get_summary(store=Depends(get_store)):
    """데이터 요약 (채팅 시스템 프롬프트에 주입되는 값과 동일)."""
    summary = compute_summary(store.list_data())
    if summary is None:
        raise NotFoundError("요약할 데이터가 없습니다.")
    return summary


@router.get("", response_model=list[DataItem])
def list_data(store=Depends(get_store)):
    return store.list_data()


@router.post("", response_model=DataItem, status_code=status.HTTP_201_CREATED)
def create_data(item: DataCreate, store=Depends(get_store)):
    return store.create_data(item)


@router.put("/{data_id}", response_model=DataItem)
def update_data(data_id: str, item: DataUpdate, store=Depends(get_store)):
    return store.update_data(data_id, item)


@router.delete("/{data_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data(data_id: str, store=Depends(get_store)):
    store.delete_data(data_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
