from core.database import PostgreSQLDatabase


async def get_db():
    async with PostgreSQLDatabase.get_session() as session:
        yield session
