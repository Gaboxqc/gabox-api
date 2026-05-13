from fastapi import APIRouter

# We add a tag so this project has its own distinct section in the /docs page
router = APIRouter(prefix="/portfolio", tags=["Gabriel Mayorga - Portfolio"])

@router.get("/")
async def get_portfolio_root():
    return {"message": "Welcome to my portfolio!"}