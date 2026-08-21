"""StatPitch domain models.

Two tables, deliberately separated by lifetime:

`statpitch_fixture` is a **cache**. It holds the three days the frontend shows
(yesterday, today, tomorrow in Nicaragua time) and is pruned past that. Nothing
in it is a permanent record.

`statpitch_settled_bet` is a **ledger**. One narrow, immutable row per settled
selection, written just before its fixture is pruned. It is what the 7- and
30-day ROI is computed from, which is the only reason those windows survive a
three-day retention policy at all.
"""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:  # pragma: no cover - import cycle, resolved at runtime
    from api.statpitch.teams import StatPitchTeam

# ==============================================================================
# STATPITCH API RESPONSE SCHEMAS
# ==============================================================================
# StatPitch promises never to rename, remove or retype an existing field, and
# asks clients to ignore unknown ones rather than validate against a closed
# schema. Every model here is therefore extra="ignore".

_IGNORE_EXTRA = ConfigDict(extra="ignore", populate_by_name=True)


class SPProbabilities(BaseModel):
    model_config = _IGNORE_EXTRA

    home: float
    draw: float
    away: float


class SPExpectedGoals(BaseModel):
    model_config = _IGNORE_EXTRA

    home: float
    away: float


class SPOverUnder(BaseModel):
    """Note the dotted JSON keys — `over_1.5`, not `over_1_5`."""

    model_config = _IGNORE_EXTRA

    over_1_5: float = PydanticField(alias="over_1.5")
    over_2_5: float = PydanticField(alias="over_2.5")
    over_3_5: float = PydanticField(alias="over_3.5")


class SPCorrectScore(BaseModel):
    model_config = _IGNORE_EXTRA

    home: int
    away: int
    probability: float


class SPRating(BaseModel):
    model_config = _IGNORE_EXTRA

    elo: float | None = None
    # club_elo | entrant_prior | pooled_prior | default
    source: str | None = None


class SPRatings(BaseModel):
    model_config = _IGNORE_EXTRA

    home: SPRating = SPRating()
    away: SPRating = SPRating()


class SPPrediction(BaseModel):
    model_config = _IGNORE_EXTRA

    probabilities: SPProbabilities
    expected_goals: SPExpectedGoals
    over_under: SPOverUnder
    # A single float — P(both teams score). There is no `no` counterpart.
    btts: float
    correct_scores: list[SPCorrectScore] = []
    ratings: SPRatings = SPRatings()
    # False means a club fell back to a prior instead of a measured Elo. The
    # number is still well formed; it is a much weaker claim.
    fully_rated: bool = True
    odds_coverage: bool = False


class SPFixture(BaseModel):
    model_config = _IGNORE_EXTRA

    fixture_id: str
    competition_id: str
    season: str | None = None
    stage: str | None = None
    format: str | None = None
    date: date
    # A bare "20:00" with no zone, or null. Not a timestamp — see
    # `StatPitchFixture.commence_time` for the instant we actually schedule on.
    kickoff: str | None = None
    date_confirmed: bool = False
    home_team: str
    away_team: str
    neutral_venue: bool = False
    odds_coverage: bool = False
    prediction: SPPrediction | None = None
    prediction_source: str | None = None
    prediction_model_version: str | None = None
    explanation: dict[str, Any] | None = None


class SPRefusal(BaseModel):
    model_config = _IGNORE_EXTRA

    available: bool = False
    reason_code: str | None = None
    reason: str | None = None
    measurement: dict[str, Any] | None = None


class SPFixturesPage(BaseModel):
    model_config = _IGNORE_EXTRA

    fixtures: list[SPFixture] = []
    count: int = 0
    total: int = 0
    offset: int = 0
    limit: int = 0
    generated_at_source: str | None = None
    model_version: str | None = None
    config_version: str | None = None
    # A refusal is a 200, not an error. NO_FIXTURE_SOURCE here means a broken
    # deploy upstream, which is not the same as a quiet day with no fixtures.
    refusal: SPRefusal | None = None


class SPHealth(BaseModel):
    model_config = _IGNORE_EXTRA

    status: str
    ready: bool = False
    artifacts_loaded: bool = False
    model_version: str | None = None
    config_version: str | None = None
    error: str | None = None


# ==============================================================================
# SELECTIONS
# ==============================================================================

# Every selection we are able to price and settle. The 1X2 three settle from
# the match outcome; the rest need the actual goal counts.
Selection = Literal[
    "home_win",
    "draw",
    "away_win",
    "over_1_5",
    "under_1_5",
    "over_2_5",
    "under_2_5",
    "over_3_5",
    "under_3_5",
    "btts_yes",
    "btts_no",
]

# Which of the two parallel track records a ledger row belongs to.
#   "1x2"     — the best 1X2 pick only
#   "overall" — the best Kelly-filtered pick across every market
BetBasis = Literal["1x2", "overall"]


# ==============================================================================
# FIXTURE CACHE  (pruned to a three-day window)
# ==============================================================================


class StatPitchFixture(SQLModel, table=True):
    """One scheduled fixture, its StatPitch prediction, and our own pricing.

    Keyed on `fixture_id`, which StatPitch builds *without* the date so a
    postponed match keeps its identity rather than appearing as a new fixture
    plus a vanished one. `match_date` is an attribute that can change.
    """

    __tablename__: str = "statpitch_fixture"
    __table_args__ = (UniqueConstraint("fixture_id", name="uq_statpitch_fixture_id"),)

    id: int | None = Field(default=None, primary_key=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    fixture_id: str = Field(index=True)
    competition_id: str = Field(index=True)
    season: str | None = Field(default=None)
    stage: str | None = Field(default=None)
    format: str | None = Field(default=None)

    # ── Scheduling ────────────────────────────────────────────────────────────
    # The Nicaragua-local day this fixture is filed under. Every "today" query
    # in the app compares against this, never against a UTC date.
    match_date: date = Field(index=True)
    # StatPitch's own nominal date, kept so a shifted fixture is diagnosable.
    source_date: date
    kickoff: str | None = Field(default=None)
    # Real UTC instant, from The Odds API. Null when we could not match the
    # fixture to an odds event — then match_date falls back to source_date.
    commence_time: datetime | None = Field(default=None)
    # True only when the schedule published a kickoff time, which is the signal
    # the date is real. False for roughly 88% of the list: those sit on a
    # matchday placeholder and must not be rendered as a specific day.
    date_confirmed: bool = Field(default=False)

    # The clubs, by reference. Names and crests live on `statpitch_team` and are
    # read back through the `home_team` / `away_team` / `*_crest_url` properties
    # below, so the JSON shape is unchanged — but there is now exactly one place
    # a club's name or badge is stored, and a crest resolved after a fixture was
    # cached is visible to it immediately rather than on the next sync.
    home_team_id: int = Field(foreign_key="statpitch_team.id", index=True)
    away_team_id: int = Field(foreign_key="statpitch_team.id", index=True)

    neutral_venue: bool = Field(default=False)
    # StatPitch's flag for whether *it* has an odds source. We price from The
    # Odds API independently, so this is informational, not a gate.
    odds_coverage: bool = Field(default=False)

    # ── Provenance ────────────────────────────────────────────────────────────
    # fitted_goal_model, or elo-poisson for the measurably weaker fallback.
    prediction_source: str | None = Field(default=None)
    model_version: str
    config_version: str | None = Field(default=None)
    # False means a club had no measured Elo and fell back to a prior.
    fully_rated: bool = Field(default=True)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Prediction ────────────────────────────────────────────────────────────
    home_xg: float
    away_xg: float
    home_elo: float | None = Field(default=None)
    away_elo: float | None = Field(default=None)
    home_elo_source: str | None = Field(default=None)
    away_elo_source: str | None = Field(default=None)
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_1_5: float
    over_2_5: float
    over_3_5: float
    btts_yes: float
    btts_no: float

    correct_scores: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    explanation: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    # ── Odds ──────────────────────────────────────────────────────────────────
    odds_home: float | None = Field(default=None)
    odds_draw: float | None = Field(default=None)
    odds_away: float | None = Field(default=None)
    odds_over_1_5: float | None = Field(default=None)
    odds_under_1_5: float | None = Field(default=None)
    odds_over_2_5: float | None = Field(default=None)
    odds_under_2_5: float | None = Field(default=None)
    odds_over_3_5: float | None = Field(default=None)
    odds_under_3_5: float | None = Field(default=None)
    odds_btts_yes: float | None = Field(default=None)
    odds_btts_no: float | None = Field(default=None)

    # ── EV and Kelly ──────────────────────────────────────────────────────────
    ev_home: float | None = Field(default=None)
    ev_draw: float | None = Field(default=None)
    ev_away: float | None = Field(default=None)
    ev_over_1_5: float | None = Field(default=None)
    ev_under_1_5: float | None = Field(default=None)
    ev_over_2_5: float | None = Field(default=None)
    ev_under_2_5: float | None = Field(default=None)
    ev_over_3_5: float | None = Field(default=None)
    ev_under_3_5: float | None = Field(default=None)
    ev_btts_yes: float | None = Field(default=None)
    ev_btts_no: float | None = Field(default=None)

    kelly_home: float | None = Field(default=None)
    kelly_draw: float | None = Field(default=None)
    kelly_away: float | None = Field(default=None)
    kelly_over_1_5: float | None = Field(default=None)
    kelly_under_1_5: float | None = Field(default=None)
    kelly_over_2_5: float | None = Field(default=None)
    kelly_under_2_5: float | None = Field(default=None)
    kelly_over_3_5: float | None = Field(default=None)
    kelly_under_3_5: float | None = Field(default=None)
    kelly_btts_yes: float | None = Field(default=None)
    kelly_btts_no: float | None = Field(default=None)

    # ── Picks ─────────────────────────────────────────────────────────────────
    # Best 1X2 pick by Kelly, and its price at sync time.
    best_bet: str | None = Field(default=None)
    best_bet_odds: float | None = Field(default=None)
    best_bet_prob: float | None = Field(default=None)

    # Best pick across every market that clears MIN_KELLY.
    best_overall_bet: str | None = Field(default=None)
    best_overall_odds: float | None = Field(default=None)
    best_overall_prob: float | None = Field(default=None)
    best_overall_ev: float | None = Field(default=None)
    best_overall_kelly: float | None = Field(default=None)

    # ── Result ────────────────────────────────────────────────────────────────
    home_score: int | None = Field(default=None)
    away_score: int | None = Field(default=None)
    actual_result: str | None = Field(default=None)
    settled_at: datetime | None = Field(default=None)
    # Set once the ledger rows exist, so pruning can never drop a fixture whose
    # track record was not banked first.
    ledgered: bool = Field(default=False, index=True)

    # ── Clubs ─────────────────────────────────────────────────────────────────
    # Two foreign keys into one table, so SQLAlchemy has to be told which is
    # which. `lazy="joined"` because every read of a fixture wants both clubs:
    # left to itself this is the query that turns a forty-fixture list into
    # eighty extra round trips.
    home: "StatPitchTeam" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "StatPitchFixture.home_team_id",
            "lazy": "joined",
        }
    )
    away: "StatPitchTeam" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "StatPitchFixture.away_team_id",
            "lazy": "joined",
        }
    )

    @property
    def home_team(self) -> str:
        return self.home.display_name

    @property
    def away_team(self) -> str:
        return self.away.display_name

    @property
    def home_crest_url(self) -> str | None:
        return self.home.crest_url

    @property
    def away_crest_url(self) -> str | None:
        return self.away.crest_url

    # ── Confidence ────────────────────────────────────────────────────────────
    # Derived, not stored: it is a pure function of the columns above, so a
    # column would only be a second copy that could fall out of step with them.
    # Exposed as properties so the read schemas pick them up by attribute.

    @property
    def _confidence(self):
        from api.statpitch.confidence import assess

        return assess(
            prediction_source=self.prediction_source,
            fully_rated=self.fully_rated,
            home_elo_source=self.home_elo_source,
            away_elo_source=self.away_elo_source,
            home_win_prob=self.home_win_prob,
            away_win_prob=self.away_win_prob,
            has_price=self.odds_home is not None,
        )

    @property
    def confidence(self) -> str:
        return self._confidence.band

    @property
    def confidence_reasons(self) -> list[str]:
        return self._confidence.reasons


# ==============================================================================
# SETTLED BET LEDGER  (permanent)
# ==============================================================================


class StatPitchSettledBet(SQLModel, table=True):
    """One settled selection. Append-only; never updated, never pruned.

    Two rows per fixture at most — one per `basis` — so the 1X2-only record and
    the multi-market Kelly record can be compared against each other rather
    than silently averaged together.
    """

    __tablename__: str = "statpitch_settled_bet"
    __table_args__ = (
        UniqueConstraint("fixture_id", "basis", name="uq_statpitch_settled_fixture_basis"),
    )

    id: int | None = Field(default=None, primary_key=True)

    fixture_id: str = Field(index=True)
    competition_id: str = Field(index=True)
    home_team: str
    away_team: str

    # Nicaragua-local match day. ROI windows are measured against this, not
    # against settlement time, so a late-recorded result lands in the right week.
    match_date: date = Field(index=True)
    settled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    basis: str = Field(index=True)
    selection: str
    probability: float
    odds_taken: float

    # Flat one unit, so ROI reads as return per unit staked and the two series
    # stay comparable. The Kelly recommendation is kept alongside it rather
    # than baked in, so a stake-weighted ROI can be derived later without
    # rewriting history.
    stake_units: float = Field(default=1.0)
    kelly_fraction: float | None = Field(default=None)

    won: bool
    pnl_units: float

    home_score: int
    away_score: int
    # Which model produced the probability. Predictions are immutable: a
    # retrain writes new rows rather than reinterpreting settled ones.
    model_version: str


# ==============================================================================
# READ SCHEMAS
# ==============================================================================


class FixtureRead(SQLModel):
    id: int
    fixture_id: str
    competition_id: str
    season: str | None
    stage: str | None
    format: str | None

    match_date: date
    source_date: date
    kickoff: str | None
    commence_time: datetime | None
    date_confirmed: bool

    home_team: str
    away_team: str
    neutral_venue: bool
    odds_coverage: bool
    home_crest_url: str | None
    away_crest_url: str | None

    prediction_source: str | None
    model_version: str
    fully_rated: bool
    synced_at: datetime

    home_xg: float
    away_xg: float
    home_elo: float | None
    away_elo: float | None
    home_elo_source: str | None
    away_elo_source: str | None
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_1_5: float
    over_2_5: float
    over_3_5: float
    btts_yes: float
    btts_no: float
    correct_scores: list[dict[str, Any]] | None
    explanation: dict[str, Any] | None

    odds_home: float | None
    odds_draw: float | None
    odds_away: float | None
    odds_over_1_5: float | None
    odds_under_1_5: float | None
    odds_over_2_5: float | None
    odds_under_2_5: float | None
    odds_over_3_5: float | None
    odds_under_3_5: float | None
    odds_btts_yes: float | None
    odds_btts_no: float | None

    ev_home: float | None
    ev_draw: float | None
    ev_away: float | None
    ev_over_1_5: float | None
    ev_under_1_5: float | None
    ev_over_2_5: float | None
    ev_under_2_5: float | None
    ev_over_3_5: float | None
    ev_under_3_5: float | None
    ev_btts_yes: float | None
    ev_btts_no: float | None

    kelly_home: float | None
    kelly_draw: float | None
    kelly_away: float | None
    kelly_over_1_5: float | None
    kelly_under_1_5: float | None
    kelly_over_2_5: float | None
    kelly_under_2_5: float | None
    kelly_over_3_5: float | None
    kelly_under_3_5: float | None
    kelly_btts_yes: float | None
    kelly_btts_no: float | None

    best_bet: str | None
    best_bet_odds: float | None
    best_bet_prob: float | None
    best_overall_bet: str | None
    best_overall_odds: float | None
    best_overall_prob: float | None
    best_overall_ev: float | None
    best_overall_kelly: float | None

    home_score: int | None
    away_score: int | None
    actual_result: str | None


class SettledBetRead(SQLModel):
    id: int
    fixture_id: str
    competition_id: str
    home_team: str
    away_team: str
    match_date: date
    settled_at: datetime
    basis: str
    selection: str
    probability: float
    odds_taken: float
    stake_units: float
    kelly_fraction: float | None
    won: bool
    pnl_units: float
    home_score: int
    away_score: int
    model_version: str


class WindowRoi(SQLModel):
    """Flat-stake performance over one rolling window, for one basis."""

    bets: int
    wins: int
    staked_units: float
    returned_units: float
    pnl_units: float
    # None rather than 0.0 when nothing settled — an empty window has no ROI,
    # and rendering it as break-even would be a claim we cannot make.
    roi_pct: float | None
    hit_rate_pct: float | None


class BasisRoi(SQLModel):
    basis: str
    week: WindowRoi
    month: WindowRoi


class ThreeDayWindow(SQLModel):
    yesterday: date
    today: date
    tomorrow: date


class StatsRead(SQLModel):
    """The stats bar: today's shape, plus rolling 7d/30d ROI per series."""

    generated_for: date
    timezone: str
    window: ThreeDayWindow

    fixtures_today: int
    fixtures_tomorrow: int
    date_confirmed_today: int
    high_confidence_today: int
    high_confidence_threshold: float
    value_bets_today: int

    roi: list[BasisRoi]


class SyncResultRead(SQLModel):
    window: ThreeDayWindow
    fetched: int
    stored: int
    priced: int
    unmatched_odds: int
    settled: int
    ledgered: int
    pruned: int
    # Clubs in the registry after this run, and how many fixture sides still
    # render without a crest.
    clubs: int = 0
    missing_crests: int = 0
    match_of_the_day: str | None = None
    model_version: str | None
    warnings: list[str] = []


# The `home` / `away` relationships name `StatPitchTeam` as a string, which
# SQLAlchemy resolves at mapper configuration — by which time the class has to
# have been imported. Importing it here, at the foot of the module rather than
# the head, makes `import api.statpitch.models` sufficient on its own: the cycle
# is safe in this direction because `teams` only needs `StatPitchFixture`, which
# is fully defined by the time this line runs.
#
# Without it the mapper fails only for callers that happen to import this module
# alone — which the app never does and a script always does.
from api.statpitch.teams import StatPitchTeam  # noqa: E402,F401  (see above)
