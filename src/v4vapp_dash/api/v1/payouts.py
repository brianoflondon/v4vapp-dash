from fastapi import APIRouter, Depends

from v4vapp_dash.api.deps import require_api_key
from v4vapp_dash.api.errors import ApiError

router = APIRouter(prefix="/v1/payouts", tags=["payouts"])


@router.post("")
async def create_payout(_key: str = Depends(require_api_key)) -> None:
    raise ApiError(501, "not_implemented", "Outgoing payouts are not enabled in v1")
