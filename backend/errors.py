"""API 오류 정의. 모든 예외는 main.py의 핸들러 하나에서 같은 JSON 형태로 변환된다."""


class AppError(Exception):
    status_code = 400
    message = "요청을 처리할 수 없습니다."

    def __init__(self, message: str | None = None):
        self.message = message or self.message
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    message = "대상을 찾을 수 없습니다."


class ConflictError(AppError):
    status_code = 409
    message = "같은 날짜의 데이터가 이미 있습니다."


class AIServiceError(AppError):
    status_code = 502
    message = "AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요."


class StoreUnavailableError(AppError):
    status_code = 503
    message = "데이터베이스 설정이 없습니다. 환경 변수를 확인하세요."
