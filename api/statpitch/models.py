from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

# ==============================================================================
# ML API RESPONSE SCHEMAS  (Pydantic only — never stored directly)
# ==============================================================================


class MLExpectedGoals(BaseModel):
    home: float
    away: float


class MLTeamInfo(BaseModel):
    home_elo: float
    away_elo: float
    elo_diff: float
    h2h_games: int
    h2h_home_wins: float  # percentage (0–100)


class MLMatchResult(BaseModel):
    home_win: float
    draw: float
    away_win: float


class MLOverUnder(BaseModel):
    over_1_5: float
    over_2_5: float
    over_3_5: float


class MLBtts(BaseModel):
    yes: float
    no: float


class MLPredictionResponse(BaseModel):
    """Exact shape of the JSON the ML model on Render returns."""

    home_team: str
    away_team: str
    expected_goals: MLExpectedGoals
    team_info: MLTeamInfo
    model_version: str
    match_result: MLMatchResult
    over_under: MLOverUnder
    btts: MLBtts


# ==============================================================================
# REQUEST SCHEMAS
# ==============================================================================


class MatchPredictionCreate(SQLModel):
    home_team: str = Field(min_length=2)
    away_team: str = Field(min_length=2)
    match_date: date | None = None
    is_neutral: bool = True  # defaults to today


class MatchPredictionBatchCreate(SQLModel):
    """Send all of today's matches in a single request."""

    matches: list[MatchPredictionCreate]
    match_date: date | None = None  # applied to all items that omit their own date


# ==============================================================================
# TABLE MODEL
# ==============================================================================


class MatchPrediction(SQLModel, table=True):
    """
    One row per match. Multiple matches can share the same date (group stage
    days have up to 4 World Cup matches). Uniqueness is enforced on the
    combination of (match_date, home_team, away_team).
    """

    __tablename__: str = "statpitch_match_prediction"
    __table_args__ = (
        UniqueConstraint("match_date", "home_team", "away_team", name="uq_match_date_teams"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # Match identity
    match_date: date = Field(index=True)  # no longer unique alone
    home_team: str
    away_team: str
    is_neutral: bool = Field(default=True)
    model_version: str
    predicted_at: datetime = Field(default_factory=datetime.utcnow)

    # Expected Goals
    home_xg: float
    away_xg: float

    # Team Info
    home_elo: float
    away_elo: float
    elo_diff: float
    h2h_games: int
    h2h_home_wins: float

    # Match Result
    home_win_prob: float
    draw_prob: float
    away_win_prob: float

    # Over/Under
    over_1_5: float
    over_2_5: float
    over_3_5: float

    # BTTS
    btts_yes: float
    btts_no: float


# ==============================================================================
# READ SCHEMA
# ==============================================================================


class MatchPredictionRead(SQLModel):
    id: int
    match_date: date
    home_team: str
    away_team: str
    is_neutral: bool
    model_version: str
    predicted_at: datetime
    home_xg: float
    away_xg: float
    home_elo: float
    away_elo: float
    elo_diff: float
    h2h_games: int
    h2h_home_wins: float
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_1_5: float
    over_2_5: float
    over_3_5: float
    btts_yes: float
    btts_no: float
