from fastapi import APIRouter,Depends
from ..security import current_user
router=APIRouter(prefix="/sync",tags=["sync"])
@router.post("/push")
def push(payload:dict,u=Depends(current_user)): return {"accepted":len(payload.get("operations",[])),"status":"ok"}
@router.post("/pull")
def pull(u=Depends(current_user)): return {"changes":[]}
