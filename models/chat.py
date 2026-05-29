from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_id: str
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
