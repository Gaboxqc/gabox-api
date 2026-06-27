from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

# ==============================================================================
# ML API RESPONSE SCHEMAS
# ==============================================================================


class MLExpectedGoals(BaseModel):
    home: float
    away: float


class MLTeamInfo(BaseModel):
    home_elo: float
    away_elo: float
    elo_diff: float
    h2h_games: int
    h2h_home_wins: float


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
    match_date: Optional[date] = None
    is_neutral: bool = True
    home_flag_url: Optional[str] = None
    away_flag_url: Optional[str] = None
    odds_home: Optional[float] = Field(default=None, gt=1.0)
    odds_draw: Optional[float] = Field(default=None, gt=1.0)
    odds_away: Optional[float] = Field(default=None, gt=1.0)


class MatchPredictionBatchCreate(SQLModel):
    matches: List[MatchPredictionCreate]
    match_date: Optional[date] = None


class MatchResultUpdate(BaseModel):
    actual_result: Literal["home_win", "draw", "away_win"]


# ==============================================================================
# TABLE MODEL
# ==============================================================================


class MatchPrediction(SQLModel, table=True):
    """
    One row per match. Stores ML probabilities, casino odds, Expected Value,
    and Kelly Criterion stake for every market (1X2, over/under, BTTS).

    EV  = (probability × odds) - 1         → is there edge?
    Kelly = (probability × odds - 1) / (odds - 1)  → how much to bet?

    best_overall_bet picks the highest-Kelly outcome across all markets
    that passes the minimum Kelly threshold. Raw EV alone is not enough —
    a +150% EV on a 5% probability event has tiny Kelly and is not worth
    the variance.
    """

    __tablename__: str = "statpitch_match_prediction"
    __table_args__ = (
        UniqueConstraint("match_date", "home_team", "away_team", name="uq_match_date_teams"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Match identity ────────────────────────────────────────────────────────
    match_date: date = Field(index=True)
    commence_time: Optional[datetime] = Field(default=None)
    home_team: str
    away_team: str
    is_neutral: bool = Field(default=True)
    home_flag_url: Optional[str] = Field(default=None)
    away_flag_url: Optional[str] = Field(default=None)
    model_version: str
    predicted_at: datetime = Field(default_factory=datetime.utcnow)

    # ── ML probabilities ──────────────────────────────────────────────────────
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

    # ── 1X2 ──────────────────────────────────────────────────────────────────
    odds_home: Optional[float] = Field(default=None)
    odds_draw: Optional[float] = Field(default=None)
    odds_away: Optional[float] = Field(default=None)
    ev_home: Optional[float] = Field(default=None)
    ev_draw: Optional[float] = Field(default=None)
    ev_away: Optional[float] = Field(default=None)
    kelly_home: Optional[float] = Field(default=None)
    kelly_draw: Optional[float] = Field(default=None)
    kelly_away: Optional[float] = Field(default=None)
    best_bet: Optional[str] = Field(default=None)  # best 1X2 pick by Kelly

    # ── Over/Under ────────────────────────────────────────────────────────────
    odds_over_1_5: Optional[float] = Field(default=None)
    odds_under_1_5: Optional[float] = Field(default=None)
    odds_over_2_5: Optional[float] = Field(default=None)
    odds_under_2_5: Optional[float] = Field(default=None)
    odds_over_3_5: Optional[float] = Field(default=None)
    odds_under_3_5: Optional[float] = Field(default=None)
    ev_over_1_5: Optional[float] = Field(default=None)
    ev_under_1_5: Optional[float] = Field(default=None)
    ev_over_2_5: Optional[float] = Field(default=None)
    ev_under_2_5: Optional[float] = Field(default=None)
    ev_over_3_5: Optional[float] = Field(default=None)
    ev_under_3_5: Optional[float] = Field(default=None)
    kelly_over_1_5: Optional[float] = Field(default=None)
    kelly_under_1_5: Optional[float] = Field(default=None)
    kelly_over_2_5: Optional[float] = Field(default=None)
    kelly_under_2_5: Optional[float] = Field(default=None)
    kelly_over_3_5: Optional[float] = Field(default=None)
    kelly_under_3_5: Optional[float] = Field(default=None)

    # ── BTTS ─────────────────────────────────────────────────────────────────
    odds_btts_yes: Optional[float] = Field(default=None)
    odds_btts_no: Optional[float] = Field(default=None)
    ev_btts_yes: Optional[float] = Field(default=None)
    ev_btts_no: Optional[float] = Field(default=None)
    kelly_btts_yes: Optional[float] = Field(default=None)
    kelly_btts_no: Optional[float] = Field(default=None)

    # ── Best overall pick ─────────────────────────────────────────────────────
    # Highest fractional-Kelly bet across all markets that passes MIN_KELLY.
    # best_overall_kelly is the FRACTIONAL Kelly (×0.25) — the actual % of
    # bankroll the model recommends staking.
    best_overall_bet: Optional[str] = Field(default=None)
    best_overall_ev: Optional[float] = Field(default=None)
    best_overall_kelly: Optional[float] = Field(default=None)

    # ── Post-match ────────────────────────────────────────────────────────────
    actual_result: Optional[str] = Field(default=None)


# ==============================================================================
# READ SCHEMAS
# ==============================================================================


class MatchPredictionRead(SQLModel):
    id: int
    match_date: date
    commence_time: Optional[datetime]
    home_team: str
    away_team: str
    is_neutral: bool
    home_flag_url: Optional[str]
    away_flag_url: Optional[str]
    model_version: str
    predicted_at: datetime

    # ML probabilities
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

    # 1X2
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    ev_home: Optional[float]
    ev_draw: Optional[float]
    ev_away: Optional[float]
    kelly_home: Optional[float]
    kelly_draw: Optional[float]
    kelly_away: Optional[float]
    best_bet: Optional[str]

    # Over/Under
    odds_over_1_5: Optional[float]
    odds_under_1_5: Optional[float]
    odds_over_2_5: Optional[float]
    odds_under_2_5: Optional[float]
    odds_over_3_5: Optional[float]
    odds_under_3_5: Optional[float]
    ev_over_1_5: Optional[float]
    ev_under_1_5: Optional[float]
    ev_over_2_5: Optional[float]
    ev_under_2_5: Optional[float]
    ev_over_3_5: Optional[float]
    ev_under_3_5: Optional[float]
    kelly_over_1_5: Optional[float]
    kelly_under_1_5: Optional[float]
    kelly_over_2_5: Optional[float]
    kelly_under_2_5: Optional[float]
    kelly_over_3_5: Optional[float]
    kelly_under_3_5: Optional[float]

    # BTTS
    odds_btts_yes: Optional[float]
    odds_btts_no: Optional[float]
    ev_btts_yes: Optional[float]
    ev_btts_no: Optional[float]
    kelly_btts_yes: Optional[float]
    kelly_btts_no: Optional[float]

    # Best overall
    best_overall_bet: Optional[str]
    best_overall_ev: Optional[float]
    best_overall_kelly: Optional[float]

    actual_result: Optional[str]


class DailyStatsRead(SQLModel):
    predictions_today: int
    high_confidence_today: int
    high_confidence_threshold: float
    value_bets_today: int
    accuracy_30d: Optional[float]
    roi_30d: Optional[float]
    settled_matches_30d: int


class SyncResultRead(SQLModel):
    synced: int
    skipped: int
    date: date
    matches: List[MatchPredictionRead]
