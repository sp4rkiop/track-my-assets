from typing import Any

from repositories.user_repository import UserRepository
from core.logger import get_logger
logger = get_logger(__name__)

class UserService:
    def __init__(self, repo: UserRepository) -> None:
        self.repo = repo

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        logger.info(f"Fetching user with id: {user_id}")
        return await self.repo.get(user_id)
