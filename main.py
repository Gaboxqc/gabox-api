from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.database import create_db_and_tables

from api.portfolio.routers import portfolio_router

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000",
                   "https://gabrielmayorga.dev",
                   "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio_router, prefix="/portfolio")

@app.get("/")
async def global_root():
    return {
        "status": "online",
        "message": "Welcome to the Gabox API. Navigate to /docs for interactive documentation."
    }