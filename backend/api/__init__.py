from fastapi import APIRouter

from . import ai, customers, geo, meta, ml, products, quality, sales

api_router = APIRouter(prefix="/api/v1")
for module in (meta, sales, products, customers, geo, ml, quality, ai):
    api_router.include_router(module.router)
