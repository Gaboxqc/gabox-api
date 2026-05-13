from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.database import create_db_and_tables

from api.portfolio.models import ProjectTag, CertificateTag, Tag, Project, ProjectTranslation, Academy, Certificate

from api.portfolio.router import router as portfolio_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield
app = FastAPI(
    title="Gabox API",
    description="A centralized serverless backend for all my portfolio projects.",
    version="1.0.0",
    lifespan=lifespan
)

# In production, replace "*" with your specific frontend domains for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio_router, prefix="/api")

@app.get("/")
async def global_root():
    return {
        "status": "online",
        "message": "Welcome to the Gabox API. Navigate to /docs for interactive documentation."
    }