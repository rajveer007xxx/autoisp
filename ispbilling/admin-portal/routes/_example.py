"""Demo: how to extract a route group from main.py into a router.

This file is NOT included yet — it's a reference for the eventual
main.py split. Copy-paste this pattern into the routes/ you want to extract.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/example", tags=["example"])

# @router.get("/hello")
# async def hello():
#     return {"ok": True, "msg": "from routes/example.py"}
