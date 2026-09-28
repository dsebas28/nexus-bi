from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..schemas.common import AppliedFilters
from ..schemas.sales import CohortCell, CustomerOverview, NewVsReturningPoint
from ..services import customers
from .deps import filters

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("/overview", response_model=CustomerOverview,
            summary="New vs returning, frequency, spend and lifetime value")
def get_overview(f: AppliedFilters = Depends(filters)):
    return CustomerOverview(filters=f, **customers.overview(f.start, f.end, f.state),
                            base=customers.base_metrics(),
                            frequency_distribution=customers.frequency_distribution())


@router.get("/monthly", response_model=list[NewVsReturningPoint], summary="New and returning customers per month")
def get_monthly(f: AppliedFilters = Depends(filters)):
    return customers.monthly_new_vs_returning(f.state)


@router.get("/cohorts", response_model=list[CohortCell], summary="Monthly cohort retention")
def get_cohorts(max_months: int = Query(6, ge=1, le=18)):
    return customers.cohorts(max_months)


@router.get("/{customer_key}", summary="Customer profile with segment, churn risk and risk factors")
def get_customer(customer_key: int = Path(..., ge=1)):
    row = customers.profile(customer_key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Customer {customer_key} not found")
    return row
