"""Demo CRUD endpoints — replace with your own resources."""

from typing import Any

from fastapi import APIRouter, HTTPException

from core.logger import get_logger
from models.item import Item, ItemCreate

router = APIRouter()
logger = get_logger(__name__)

_items: dict[int, Item] = {}
_next_id = 1


@router.get("/items", response_model=list[Item])
async def list_items() -> list[Item]:
    """Return all items."""
    return list(_items.values())


@router.post("/items", response_model=Item, status_code=201)
async def create_item(body: ItemCreate) -> Item:
    """Create a new item."""
    global _next_id
    item = Item(id=_next_id, **body.model_dump())
    _items[_next_id] = item
    _next_id += 1
    logger.info("item.created", extra={"item_id": item.id})
    return item


@router.get("/items/{item_id}", response_model=Item)
async def get_item(item_id: int) -> Item:
    """Fetch a single item by ID."""
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    return _items[item_id]


@router.put("/items/{item_id}", response_model=Item)
async def update_item(item_id: int, body: ItemCreate) -> Item:
    """Update an existing item."""
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    item = Item(id=item_id, **body.model_dump())
    _items[item_id] = item
    logger.info("item.updated", extra={"item_id": item_id})
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: int) -> None:
    """Delete an item."""
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    del _items[item_id]
    logger.info("item.deleted", extra={"item_id": item_id})
