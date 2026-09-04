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

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as PydanticField
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:  # pragma: no cover - import cycle, resolved at runtime
    from api.statpitch.competitions import StatPitchCompetition
    from api.statpitch.teams import StatPitchTeam

# ==============================================================================
# STATPITCH API RESPONSE SCHEMAS
# ==============================================================================
# StatPitch promises never to rename, remove or retype an existing field, and
# asks clients to ignore unknown ones rather than validate against a closed
# schema. Every model here is therefore extra="ignore".

# `protected_namespaces=()` because StatPitch publishes `model_version`,
# `model_edge` and `model_odds`. Pydantic reserves the `model_` prefix for its
# own methods and warns on every one of them; the fields are upstream's names
# and cannot be renamed without breaking the mapping.
_IGNORE_EXTRA = ConfigDict(extra="ignore", populate_by_name=True, protected_namespaces=())


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
# STATPITCH PRICING AND SELECTION
# ==============================================================================
# StatPitch prices its own card now, against a 25-book panel it did not have
# before. These are the shapes behind `/bets/today`, `/card/upcoming` and
# `/odds/matchday`.
#
# Captured against config `dec-2026.08.1-experimental`, schema_version 1, on
# 2026-09-01. Three fields the integration contract describes — `selection_basis`,
# `pricing` and `model_odds` — are **not published by the live service yet**.
# They are declared here as optional so the day they ship the sync fills them
# with no migration and no code change, and null until then. Nothing infers
# them: a guessed `pricing` would be the one field capable of turning our own
# opinion into a price we claim a bookmaker offered.


class SPSelection(BaseModel):
    """One priced, graded selection.

    The same shape in `bets[]`, in `assessments[]` and inside a matchday
    `markets` family, which is why it is one class and not three.

    The four prices are kept apart on purpose and must never be collapsed:
    `reference_odds` is the benchmark book the rule measures against,
    `consensus_odds` the panel mean, `fair_odds` that consensus de-vigged, and
    `odds` the best quote — the only one of the four anybody can actually bet.
    """

    model_config = _IGNORE_EXTRA

    fixture_id: str
    competition_id: str | None = None
    # Present on every selection row, which is what lets a price be joined to a
    # fixture by name-free identity rather than by fuzzy matching.
    home_team: str | None = None
    away_team: str | None = None

    # "1x2_home" | "1x2_draw" | "1x2_away" — see `selections.STATPITCH_TO_OURS`.
    selection: str
    market_family: str
    # The handicap or totals line. Null on 1X2, which is the only family the
    # service currently publishes.
    line: float | None = None
    description: str | None = None

    # ── The prices, deliberately never merged ────────────────────────────────
    reference_odds: float | None = None
    consensus_odds: float | None = None
    odds: float | None = None
    fair_odds: float | None = None

    # ── Probabilities ────────────────────────────────────────────────────────
    p_model: float | None = None
    q_fair: float | None = None
    # What the staking actually used. Equals `q_fair` while the market-shrinkage
    # weight fits at 0.000, which is why `model_edge` is zero on every row.
    p_used: float | None = None

    # ── Edges, decomposed and never summed ───────────────────────────────────
    edge_prob: float | None = None
    expected_value: float | None = None
    price_edge: float | None = None
    model_edge: float | None = None
    # Best quote against the benchmark book. This is what drives selection.
    rule_edge: float | None = None

    rule_qualified: bool = False
    grade: str | None = None
    composite: float | None = None
    # 0.0 means assessed, not recommended. The card's own note names this as
    # the field that separates analysis from recommendation.
    stake_fraction: float = 0.0
    # Why this selection was refused, in readable prose. Empty on a bet.
    reasons: list[str] = []

    # Not published as of 2026-09-01 — see the note above this section.
    selection_basis: str | None = None
    pricing: str | None = None
    model_odds: float | None = None

    @property
    def recommended(self) -> bool:
        """Whether this row is a pick rather than an assessment.

        Derived from the stake rather than read from the matchday payload's own
        `recommended` list, whose element type cannot be told from the empty
        list the service currently returns. `stake_fraction` is documented
        upstream as the discriminator, so it is the one to trust.
        """
        return self.stake_fraction > 0


class SPSelectionRule(BaseModel):
    """The rule that decided the card, and how much it has been measured.

    `status` is the field that has to survive onto every stored row: when the
    rule is promoted to `fitted`, history must still show what it was
    recommended under.
    """

    model_config = _IGNORE_EXTRA

    # experimental | candidate | fitted
    status: str | None = None
    # The benchmark book, e.g. `odds_pinnacle`. Worth storing: the live feed
    # does not currently carry Pinnacle, and the candidate reference is an
    # exchange, so this is not a constant.
    reference: str | None = None
    threshold: float | None = None
    market_families: list[str] = []
    max_per_day: int | None = None
    evidence: str | None = None

    # Where the rule has been *measured* to earn, and therefore the only
    # competitions that can produce a bet. Not every priced league qualifies:
    # StatPitch prices eight and this currently names six.
    #
    # This is the authoritative answer to "why does this league never have
    # picks", and there is no other way to ask. An empty slate returns
    # `NO_QUALIFYING_SELECTION` whether the league is outside the scope or
    # simply had nothing qualify today, so absence is not readable — the scope
    # has to be read directly.
    #
    # Re-measured upstream, so it moves. The Primeira Liga sits just outside on
    # sample size rather than on a negative estimate and is expected to be
    # reconsidered, which is why nothing derives this from a local constant.
    competitions: list[str] = []
    # Undocumented upstream but published. Carried so it lands in the stored
    # rule blob rather than being dropped on the floor.
    fallback_enabled: bool | None = None


class SPEmptyBecause(BaseModel):
    """Why a day produced no bet. A quiet day, not a failure."""

    model_config = _IGNORE_EXTRA

    card_dates: list[date] = []
    fixtures_today: int = 0
    card_covers_today: bool = False
    cause: str | None = None
    note: str | None = None


class SPBetsToday(BaseModel):
    """`GET /bets/today` — the daily pick, or a reasoned absence.

    Note `refusal`: unlike the one on `/fixtures/upcoming`, this is **not
    fatal**. It carries `SELECTION_RULE_EXPERIMENTAL`, which is a standing
    statement about the rule's calibration rather than a broken deploy — it is
    present on every response while the rule is experimental, and the card
    returns real bets alongside it. Treating it the way the fixtures refusal is
    treated would fail every sync.
    """

    model_config = _IGNORE_EXTRA

    # Aliased rather than named `date`: assigning a default to a field of that
    # name shadows the `date` type inside the class body, and the annotation
    # then evaluates to `None | None`.
    for_date: date | None = PydanticField(default=None, alias="date")
    bets: list[SPSelection] = []
    count: int = 0
    total_exposure: float = 0.0
    assessed: int = 0
    qualified_by_rule: int = 0
    selection_rule: SPSelectionRule = SPSelectionRule()
    config_status: str | None = None

    # The strings that have to reach the reader. `caveat` explains what the
    # rule's status means; `confidence_caveat` is not published yet.
    caveat: str | None = None
    confidence_caveat: str | None = None
    disclaimer: str | None = None

    by_basis: dict[str, int] = {}
    # Why the day is empty. `/card/today` names it `binding_constraint`;
    # `/bets/today` says it in `reason` plus the structured `empty_because`.
    binding_constraint: str | None = None
    empty_because: SPEmptyBecause | None = None
    reason: str | None = None
    refusal: SPRefusal | None = None

    model_version: str | None = None
    config_version: str | None = None
    generated_at: datetime | None = None


class SPCard(BaseModel):
    """`GET /card/upcoming` — the forward slate, and the sync source.

    Returns both the recommendations and everything priced and graded, so one
    call fills a whole fixtures view. `/card/today` is deliberately not used:
    prices publish days ahead, so on a quiet day it returns nothing while a
    full slate sits in the card.
    """

    model_config = _IGNORE_EXTRA

    # `from` is a Python keyword, so both dates are aliased.
    from_date: date | None = PydanticField(default=None, alias="from")
    to_date: date | None = PydanticField(default=None, alias="to")

    bets: list[SPSelection] = []
    assessments: list[SPSelection] = []
    assessed: int = 0
    total_exposure: float = 0.0
    grades: dict[str, int] = {}
    dates_covered: list[date] = []
    reason: str | None = None
    note: str | None = None
    card_generated_at: datetime | None = None
    disclaimer: str | None = None

    model_version: str | None = None
    config_version: str | None = None
    generated_at: datetime | None = None


class SPMatchdayFixture(BaseModel):
    """One fixture's prices, grouped by market family."""

    model_config = _IGNORE_EXTRA

    fixture_id: str
    competition_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    # "2026-09-02 18:45:00" — space-separated and without an offset, but
    # documented UTC by its own name. This is the real instant the whole
    # local-day bucket depends on, and the only source of one now that we no
    # longer fetch bookmaker odds for the schedule.
    kickoff_utc: datetime | None = None

    markets: dict[str, list[SPSelection]] = {}
    markets_priced: list[str] = []

    @field_validator("kickoff_utc")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        """Attach UTC to the naive timestamp rather than letting it drift.

        `to_local_date` would read a naive value as UTC anyway, but leaving it
        naive means anything else that touches it has to know that rule too.
        """
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class SPMatchdayOdds(BaseModel):
    """`GET /odds/matchday` — a day's prices, per fixture.

    1X2 is captured daily for every competition; totals and handicaps only for
    competitions playing that day, because the price API bills per market per
    competition. `markets_priced` says which arrived.
    """

    model_config = _IGNORE_EXTRA

    # Aliased for the same reason as `SPBetsToday.for_date`.
    for_date: date | None = PydanticField(default=None, alias="date")
    fixtures: list[SPMatchdayFixture] = []
    count: int = 0
    selections: int = 0
    note: str | None = None
    card_generated_at: datetime | None = None
    disclaimer: str | None = None

    model_version: str | None = None
    config_version: str | None = None
    generated_at: datetime | None = None


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

# Which parallel track record a ledger row belongs to.
#   "1x2"     — our best 1X2 pick only
#   "overall" — our best Kelly-filtered pick across every market
#   "rule"    — StatPitch's own selection rule, staked by StatPitch
#
# The contract also describes a "confidence" tier — the most likely outcome on
# a day nothing cleared the rule, measured at -2.12% ROI and flat-staked. It is
# deliberately absent here: the live service publishes no `selection_basis`, so
# nothing would ever be tagged with it and the series would read as a permanent
# empty window rather than as a tier that has not shipped. Add it when a row
# arrives carrying it.
BetBasis = Literal["1x2", "overall", "rule"]


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
    # References the registry by its natural key. `competition_id` is already
    # stable and already what every filter matches on, so pointing the key at it
    # normalises the name and icon away without renaming a column or touching a
    # single query.
    competition_id: str = Field(
        foreign_key="statpitch_competition.competition_id", index=True, max_length=64
    )
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

    # Joined for the same reason the clubs are: every read of a fixture wants
    # the competition's name, and one query is better than one per row.
    competition: "StatPitchCompetition" = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": (
                "StatPitchFixture.competition_id == StatPitchCompetition.competition_id"
            ),
            "foreign_keys": "StatPitchFixture.competition_id",
            "lazy": "joined",
            "viewonly": True,
        }
    )

    # StatPitch's own priced selections for this fixture.
    #
    # `selectin` rather than `joined`, unlike every relationship above it. Those
    # are all many-to-one and add a column; this is a collection, and joining it
    # beside two joined clubs and a joined competition would multiply every
    # fixture row by its selection count and make the others arrive three times
    # over. `selectin` costs one extra query for the whole page instead.
    #
    # `viewonly` because the sync writes these explicitly, in its own upsert,
    # after the fixture exists — not by cascading off this attribute.
    selections: list["StatPitchSelection"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "StatPitchFixture.fixture_id == StatPitchSelection.fixture_id",
            "foreign_keys": "StatPitchSelection.fixture_id",
            "order_by": "StatPitchSelection.selection",
            "lazy": "selectin",
            "viewonly": True,
        }
    )

    @property
    def competition_name(self) -> str:
        return self.competition.name

    @property
    def competition_short_name(self) -> str:
        return self.competition.short_name

    @property
    def competition_icon_url(self) -> str | None:
        return self.competition.icon_url

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
# STATPITCH'S OWN SELECTIONS  (pruned with the fixture)
# ==============================================================================


class StatPitchSelection(SQLModel, table=True):
    """One StatPitch-priced selection, stored as it arrived.

    A separate table rather than more columns on the fixture. Each selection
    carries four prices, three probabilities and five edges; across even the
    three 1X2 outcomes that is fifty columns, and the fixture row is already
    the widest thing in the schema.

    Keyed on `(fixture_id, selection)` and refreshed in place, which departs
    from the contract's suggested `(fixture_id, selection, captured_at)`. That
    key is for a system keeping price history; this table is a cache that is
    pruned with its fixture after three days, and the permanent record lives in
    `statpitch_settled_bet`. `captured_at` is kept as a column so the age of a
    price is still legible — it is just not part of the identity.
    """

    __tablename__: str = "statpitch_selection"
    __table_args__ = (UniqueConstraint("fixture_id", "selection", name="uq_statpitch_selection"),)

    id: int | None = Field(default=None, primary_key=True)

    # CASCADE, unlike the ledger's RESTRICT: these rows are cache. When the
    # fixture is pruned they should go with it rather than hold it back.
    fixture_id: str = Field(
        foreign_key="statpitch_fixture.fixture_id", ondelete="CASCADE", index=True
    )

    # StatPitch's own name, stored verbatim — "1x2_home", not "home_win".
    selection: str = Field(index=True)
    # Ours, from `selections.translate`. Null when we have no name for it, which
    # is how a selection ends up displayed but never priced or settled.
    our_selection: str | None = Field(default=None, index=True)
    market_family: str = Field(index=True)
    line: float | None = Field(default=None)
    description: str | None = Field(default=None)

    # ── The four prices, never merged ─────────────────────────────────────────
    # `odds` is the only one anybody can bet. `fair_odds` and `model_odds` are
    # opinions, and `reference_odds` is a benchmark, not an offer.
    reference_odds: float | None = Field(default=None)
    consensus_odds: float | None = Field(default=None)
    odds: float | None = Field(default=None)
    fair_odds: float | None = Field(default=None)

    # ── Probabilities ─────────────────────────────────────────────────────────
    p_model: float | None = Field(default=None)
    q_fair: float | None = Field(default=None)
    p_used: float | None = Field(default=None)

    # ── Edges, decomposed ─────────────────────────────────────────────────────
    edge_prob: float | None = Field(default=None)
    expected_value: float | None = Field(default=None)
    price_edge: float | None = Field(default=None)
    # Zero on every row while the shrinkage weight fits at 0.000. Stored anyway:
    # the day it is non-zero is the day the model starts picking.
    model_edge: float | None = Field(default=None)
    rule_edge: float | None = Field(default=None)

    rule_qualified: bool = Field(default=False, index=True)
    grade: str | None = Field(default=None)
    composite: float | None = Field(default=None)
    # 0.0 means assessed, not recommended.
    stake_fraction: float = Field(default=0.0, index=True)
    reasons: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    # ── Per-row provenance ────────────────────────────────────────────────────
    # On the row, never once per sync. When the rule is promoted to `fitted`,
    # a historical row must still show what it was recommended under — and a
    # column filled at read time from today's status could not do that.
    config_status: str | None = Field(default=None)
    selection_rule_status: str | None = Field(default=None)
    # The benchmark book. Not a constant: the live feed does not currently carry
    # Pinnacle and the candidate reference is an exchange.
    selection_rule_reference: str | None = Field(default=None)

    # Not published upstream as of 2026-09-01. Nullable rather than inferred.
    selection_basis: str | None = Field(default=None, index=True)
    pricing: str | None = Field(default=None, index=True)
    model_odds: float | None = Field(default=None)

    # When StatPitch built the card this came from, not when we stored it.
    captured_at: datetime | None = Field(default=None)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def bettable(self) -> bool:
        """Whether `odds` is a price somebody is actually offering.

        While `pricing` is unpublished this can only be answered by the price
        being present at all. Once upstream ships the field, a `model` row is
        excluded here — its `odds` is `1 / p_model`, our own opinion wearing a
        price's clothes.
        """
        if self.odds is None or self.odds <= 1:
            return False
        return self.pricing != "model"


# ==============================================================================
# THE DAILY PICK  (one row per local day, pruned with the window)
# ==============================================================================


class StatPitchBetDay(SQLModel, table=True):
    """What `/bets/today` said about one day, cached.

    The caveats are the reason this table exists. They are day-level strings
    with no home on a fixture row, and they are not decoration: a reader shown
    a pick without the caveat that qualifies it has been told something untrue.
    Caching them means they still serve when the upstream free instance is
    asleep, which is the moment they would otherwise go missing.
    """

    __tablename__: str = "statpitch_bet_day"
    __table_args__ = (UniqueConstraint("match_date", name="uq_statpitch_bet_day_date"),)

    id: int | None = Field(default=None, primary_key=True)

    # Nicaragua-local day, matching every other date in the module.
    match_date: date = Field(index=True)

    count: int = Field(default=0)
    assessed: int = Field(default=0)
    qualified_by_rule: int = Field(default=0)
    total_exposure: float = Field(default=0.0)

    # ── The strings that have to reach the reader ─────────────────────────────
    caveat: str | None = Field(default=None)
    # Not published upstream yet. When it is, it accompanies every tier-2 pick
    # and naming it here means the sync stores it without a migration.
    confidence_caveat: str | None = Field(default=None)
    disclaimer: str | None = Field(default=None)
    # Why the day is empty, when it is. An empty day is a normal answer.
    reason: str | None = Field(default=None)
    binding_constraint: str | None = Field(default=None)
    empty_because: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # ── Provenance ────────────────────────────────────────────────────────────
    config_status: str | None = Field(default=None)
    selection_rule_status: str | None = Field(default=None)
    selection_rule: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    # The rule's measured scope, lifted out of the blob above into its own
    # column because it is the one part of the rule that gets *queried* rather
    # than displayed: it decides whether a competition can produce a bet at all,
    # and a JSON blob is a poor place to ask that from.
    #
    # Stored per day rather than per selection. The rule is a property of the
    # day's card, and the day row is already the thing that records what a day
    # was produced under — so a scope change on a later re-measurement leaves
    # every earlier day reading correctly.
    selection_rule_competitions: list[str] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    by_basis: dict[str, int] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    # The advisory refusal, which is a caveat rather than a failure. Stored so
    # its reason code is visible without re-deriving it from prose.
    refusal_reason_code: str | None = Field(default=None)
    refusal_reason: str | None = Field(default=None)

    model_version: str | None = Field(default=None)
    config_version: str | None = Field(default=None)
    generated_at: datetime | None = Field(default=None)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    competition_id: str = Field(
        foreign_key="statpitch_competition.competition_id", index=True, max_length=64
    )
    # RESTRICT, not CASCADE. This is a permanent record: a club must not be
    # removable while its results are still on the books, and deleting the
    # history to make a club deletable is exactly the wrong trade.
    home_team_id: int = Field(foreign_key="statpitch_team.id", ondelete="RESTRICT", index=True)
    away_team_id: int = Field(foreign_key="statpitch_team.id", ondelete="RESTRICT", index=True)

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

    # ── Provenance, for rows that came from StatPitch's own rule ──────────────
    # Null on our own `1x2` and `overall` rows, which are priced here and owe
    # nothing to an upstream rule. On a `rule` row these are what the bet was
    # recommended under, frozen at settlement: when the rule is promoted to
    # `fitted`, everything already banked must still read as experimental.
    selection_basis: str | None = Field(default=None)
    pricing: str | None = Field(default=None)
    config_status: str | None = Field(default=None)
    selection_rule_status: str | None = Field(default=None)

    home: "StatPitchTeam" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "StatPitchSettledBet.home_team_id",
            "lazy": "joined",
        }
    )
    away: "StatPitchTeam" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "StatPitchSettledBet.away_team_id",
            "lazy": "joined",
        }
    )

    @property
    def home_team(self) -> str:
        return self.home.display_name

    @property
    def away_team(self) -> str:
        return self.away.display_name


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


class SelectionRead(SQLModel):
    """One StatPitch-priced selection, as a client sees it.

    Every price is exposed separately and none is presented as *the* price.
    That is the point: `odds` is the best quote and the only bettable number,
    `reference_odds` is the benchmark the rule measured against,
    `consensus_odds` is the panel mean and `fair_odds` is that mean de-vigged.
    Collapsing them into one figure would throw away the only evidence a reader
    has for whether a price is actually good.
    """

    model_config = ConfigDict(from_attributes=True)  # type: ignore[assignment]

    selection: str
    our_selection: str | None
    market_family: str
    line: float | None
    description: str | None

    reference_odds: float | None
    consensus_odds: float | None
    odds: float | None
    fair_odds: float | None

    p_model: float | None
    q_fair: float | None
    p_used: float | None

    expected_value: float | None
    price_edge: float | None
    model_edge: float | None
    rule_edge: float | None

    rule_qualified: bool
    grade: str | None
    # 0.0 means assessed, not recommended. The field that separates analysis
    # from a recommendation, and the one to filter on.
    stake_fraction: float
    # Why it was refused, in readable prose. Empty on a pick.
    reasons: list[str] | None

    # Per-row provenance, so a row still reads as what it was recommended under
    # after the rule is promoted.
    config_status: str | None
    selection_rule_status: str | None
    selection_rule_reference: str | None

    # Null until StatPitch publishes them; see the SP section above.
    selection_basis: str | None
    pricing: str | None
    model_odds: float | None

    captured_at: datetime | None


class BetPickRead(SelectionRead):
    """A staked selection, with enough of its fixture to render on its own.

    `/bets/today` is read without a fixture list beside it, so the clubs and the
    kickoff travel with the pick rather than being looked up separately.
    """

    fixture_id: str
    competition_id: str
    competition_name: str
    competition_short_name: str
    competition_icon_url: str | None
    home_team: str
    away_team: str
    home_crest_url: str | None
    away_crest_url: str | None
    match_date: date
    commence_time: datetime | None


class BetsTodayRead(SQLModel):
    """Today's pick, or a reasoned absence — served from cache.

    `caveat` is not decoration and is never null while a pick is present. A
    reader shown a recommendation without the statement qualifying it has been
    told something untrue, so the endpoint synthesises one from the stored rule
    status rather than return a pick bare. Render it.
    """

    match_date: date

    bets: list[BetPickRead]
    count: int
    assessed: int
    qualified_by_rule: int
    total_exposure: float

    # Always present when `bets` is non-empty.
    caveat: str | None
    # Not published upstream yet. When it is, it accompanies every tier-2 pick
    # and must be rendered beside one.
    confidence_caveat: str | None
    disclaimer: str | None

    # Why the day is empty, when it is. An empty day is a normal answer here,
    # not a failure — most days produce no qualifying bet at all.
    reason: str | None
    binding_constraint: str | None
    empty_because: dict[str, Any] | None

    by_basis: dict[str, int] | None
    selection_rule: dict[str, Any] | None
    # Which competitions the rule is measured to earn in. Surfaced as its own
    # field rather than left inside `selection_rule`, because it is the only
    # way to tell "this league is outside the scope" from "nothing qualified
    # today" — both of which otherwise look like an empty slate.
    selection_rule_competitions: list[str] | None
    config_status: str | None
    selection_rule_status: str | None

    model_version: str | None
    config_version: str | None
    generated_at: datetime | None
    synced_at: datetime | None


class CompetitionRead(SQLModel):
    """One competition, for filter chips and headings.

    Public and ungated: which competitions exist is navigation, not product.
    """

    competition_id: str
    name: str
    short_name: str
    icon_url: str | None = None
    # Whether the free tier can see it, so the frontend can mark the seven cups
    # as an upgrade rather than discovering it by getting an empty list back.
    free_tier: bool = False


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
    # Our own Kelly picks, and StatPitch's staked rule selections. Counted
    # apart because they are two different strategies, not two views of one.
    value_bets_today: int
    rule_bets_today: int = 0

    roi: list[BasisRoi]


class SyncResultRead(SQLModel):
    window: ThreeDayWindow
    fetched: int
    stored: int
    priced: int
    # Renamed from `unmatched_odds`, which described a name-matching failure
    # that can no longer happen: prices arrive keyed by `fixture_id`. A fixture
    # is unpriced because the feed has not published its matchday block yet.
    unpriced: int
    # StatPitch selection rows stored, and how many of them it staked.
    selections: int = 0
    rule_bets: int = 0
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


# The relationships below name `StatPitchTeam` and `StatPitchCompetition` as
# strings, which SQLAlchemy resolves at mapper configuration — by which time
# those classes have to have been imported somewhere. Importing them here, at
# the foot of the module, makes `import api.statpitch.models` sufficient on its
# own; without it the mapper fails for any caller that imports this module
# alone, which the app never does and a script always does.
#
# Module imports rather than `from ... import Class`, and that distinction is
# load-bearing. `teams` imports `models`, so whichever is imported first finds
# the other half-built — and a `from` import would then ask a partially
# initialised module for a class it has not defined yet. Binding the module
# asks it for nothing, and the class is registered by the time the mapper looks.
import api.statpitch.competitions  # noqa: E402,F401  (see above)
import api.statpitch.teams  # noqa: E402,F401  (see above)
