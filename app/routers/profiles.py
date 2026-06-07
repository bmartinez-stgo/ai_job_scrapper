from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import RoleProfile

router = APIRouter()


@router.get("/api/profiles")
async def list_profiles(db: Session = Depends(get_db)):
    profiles = db.query(RoleProfile).filter(RoleProfile.is_active == True).all()
    return {"profiles": [{"id": p.id, "name": p.name, "focus": p.focus, "market": p.market} for p in profiles]}
