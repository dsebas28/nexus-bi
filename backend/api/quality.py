from fastapi import APIRouter, HTTPException

from ..schemas.ml import DataQualityResponse
from ..services import quality

router = APIRouter(tags=["Data quality"])


@router.get("/data-quality", response_model=DataQualityResponse,
            summary="Data-quality report of the latest successful pipeline run")
def get_data_quality():
    summary = quality.summary()
    if summary is None or summary["run_id"] is None:
        raise HTTPException(status_code=404, detail="No pipeline run found. Run: python -m data_pipeline")
    return DataQualityResponse(**summary, issues=quality.issues())
