from fastapi import HTTPException


class AppException(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field: str = None
    ):
        self.code = code
        self.message = message
        self.field = field

        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "field": field
            }
        )