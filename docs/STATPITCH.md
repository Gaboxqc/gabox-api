# StatPitch on GaboxAPI

What this API stores, how it decides a bet, and what the frontend can read.
Written for whoever builds the UI, and for whoever has to debug a sync at 06:00.

The upstream prediction service has its own reference in the StatPitch repo
(`docs/API.md`). This document covers **our** side: what we keep, for how long,
and why the numbers mean what they mean.

**`/openapi.json` is the authority on shapes.** Where this file and the schema
disagree, the schema is right — it is generated from the models, this is written
by hand. What you get here that the schema cannot express: which scale a number
is on, which nulls are normal, and why.

---

## Contents

1. [Before you start](#1-before-you-start)
2. [The three-day window](#2-the-three-day-window)
3. [Reading a fixture](#3-reading-a-fixture)
4. [Endpoints](#4-endpoints)
5. [Performance and the ledger](#5-performance-and-the-ledger)
6. [The sync](#6-the-sync)
7. [Configuration](#7-configuration)
8. [Operations](#8-operations)

---

## 1. Before you start

Four things that shape everything below.

**StatPitch supplies probabilities, and nothing else.** It is stateless, and it
declines to recommend a bet on purpose: its measured shrinkage weight against
the closing line is 0.000, so `/best-bet`, `/card/today` and `/value-bets/today`
upstream all refuse by design. It also has **no results endpoint**.

So the three things ROI actually needs come from three different places:

| Needed | Source | Note |
|---|---|---|
| A selection | Ours, *and* StatPitch's | Two independent series, never averaged |
| A real price | StatPitch `/card/upcoming` | 25-book panel; `odds` is the best quote |
| A final score | ESPN `/scoreboard` | StatPitch has no results endpoint |

**The predictions are StatPitch's. The bets are both.** StatPitch now prices its
own card and stakes its own rule, so there are three parallel track records:
`1x2` and `overall` are ours (EV and quarter-Kelly over its probabilities), and
`rule` is StatPitch's own selection measured at its own numbers. They are
tagged, never merged, so it stays possible to tell which one earned.

A price arrives keyed by `fixture_id`, so nothing is joined by club name any
more — see §8, where matching now covers results only.

**Fixtures are temporary, the record is permanent.** Two tables, two lifetimes —
see [The three-day window](#2-the-three-day-window).

**Four sets of competitions, and they are not the same set.** They were, until
StatPitch added three leagues it can price. Collapsing any two of these back
into one constant is how the free tier silently gains a league:

| set | n | what it means |
|---|---|---|
| all competitions | 15 | everything StatPitch serves |
| priced | 8 | has a market — what we sync |
| stakeable | 6 | measured to earn, so a bet is possible |
| free tier | 5 | a promise on the pricing page |

| competition_id | name | priced | stakeable | free |
|---|---|---|---|---|
| `ENG.PL` | Premier League | yes | yes | yes |
| `ESP.LALIGA` | LaLiga | yes | yes | yes |
| `GER.BUNDESLIGA` | Bundesliga | yes | yes | yes |
| `ITA.SERIEA` | Serie A | yes | yes | yes |
| `FRA.LIGUE1` | Ligue 1 | yes | yes | yes |
| `TUR.SUPERLIG` | Super Lig | yes | yes | no |
| `POR.PRIMEIRA` | Primeira Liga | yes | **no** | no |
| `NED.EREDIVISIE` | Eredivisie | yes | **no** | no |
| the seven cups | — | no | no | no |

The Eredivisie and Primeira Liga are served in full — fixtures, predictions,
prices — and can never produce a bet. That is a **measurement, not a gap**: the
Eredivisie's own CLV estimate is negative (−0.22%, t=−0.82) and the Primeira
Liga's is positive but unresolvable at n=974 (+0.27%, t=+1.07). The second may
return on a later re-measurement, so neither is hard-coded as permanent.

Note the ID prefixes: `POR`/`NED`/`TUR` are Club Elo ISO-3 country codes, not
the ISO-2 codes you might expect. Treat the whole string as opaque and match it
exactly.

Beware one trap when reading an empty slate: **no reason code distinguishes
"this league is outside the rule's scope" from "nothing qualified today"** —
both come back as `NO_QUALIFYING_SELECTION`. The authoritative answer is
`selection_rule_competitions` on `/bets/today`, which we store per day and serve
as its own field. Read it; do not infer scope from absence.

`empty_because.cause` separates the two commonest empty days:
`fixtures_today_carry_no_price` means the feed has not published this matchday
block yet, while `assessed_but_nothing_qualified` means it did and nothing
cleared the rule. The first resolves itself; the second is the normal case.

`selection_rule_competitions` is `null` for any day synced before the field
shipped. That means "not recorded", not "scope of nothing" — `STAKEABLE_LEAGUES`
in `leagues.py` is the fallback, and it is only ever a fallback: the scope is
re-measured upstream and moves.

---

## 2. The three-day window

The frontend shows yesterday, today and tomorrow. Fixtures outside that window
are deleted.

That conflicts with a 7- and 30-day ROI, so there are **two tables with
deliberately different lifetimes**:

| Table | Lifetime | Purpose |
|---|---|---|
| `statpitch_fixture` | 3 days, then pruned | Everything the frontend renders |
| `statpitch_settled_bet` | Forever, append-only | The track record ROI is computed from |

A fixture is settled and its ledger rows banked **before** it becomes eligible
for pruning, and pruning refuses to drop a fixture that still owes the ledger a
row. Retention and the track record are therefore independent by construction,
not by getting the scheduling right.

The practical consequence: **never compute performance from
`/statpitch/fixtures`.** It only ever holds three days. Use `/statpitch/stats`
or `/statpitch/ledger`.

### "Today" is a Nicaragua day

The rollover is local midnight in `STATPITCH_TIMEZONE` (default
`America/Managua`, UTC-6, no daylight saving) — **not** the server's UTC day.
Between 18:00 and midnight local the two disagree, and every date in this API
follows the local one.

`GET /statpitch/fixtures/window` returns the three dates the API currently
considers live, so the UI never has to compute them:

```json
{ "yesterday": "2026-08-17", "today": "2026-08-18", "tomorrow": "2026-08-19" }
```

Prefer this over deriving dates client-side from the browser's clock — a user in
another timezone would otherwise ask for a day the cache does not hold.

---

## 3. Reading a fixture

A fixture object has 81 fields, in seven groups.

| Group | Fields | Notes |
|---|---|---|
| Identity | `id`, `fixture_id`, `competition_id`, `season`, `stage`, `format` | `id` is the numeric key `/fixtures/{id}` takes; `fixture_id` is the composite natural key |
| Scheduling | `match_date`, `source_date`, `kickoff`, `commence_time`, `date_confirmed` | |
| Teams | `home_team`, `away_team`, `neutral_venue`, `home_crest_url`, `away_crest_url` | |
| Provenance | `prediction_source`, `model_version`, `fully_rated`, `synced_at`, `odds_coverage` | `model_version` is always present; `prediction_source` can be null |
| Prediction | `home_xg`, `away_xg`, `*_elo`, `*_elo_source`, `home_win_prob`, `draw_prob`, `away_win_prob`, `over_1_5`, `over_2_5`, `over_3_5`, `btts_yes`, `btts_no`, `correct_scores`, `explanation` | eight probabilities — **there are no `under_*` probabilities**, see below |
| Pricing | `odds_*`, `ev_*`, `kelly_*` across 11 markets | all null when unpriced; individually null per unquoted market |
| Picks and result | `best_bet`, `best_bet_odds`, `best_bet_prob`, `best_overall_bet`, `best_overall_odds`, `best_overall_prob`, `best_overall_ev`, `best_overall_kelly`, `home_score`, `away_score`, `actual_result` | the two pick groups are **not** symmetric |

### Scales, and how to get them wrong

This is the single most common source of a rendering bug, because the two
scales look alike and neither is labelled.

| Field | Scale | Example |
|---|---|---|
| `*_prob`, `over_*`, `btts_*`, `best_*_prob` | **0–1 fraction** | `0.7283` is 72.83% |
| `ev_*`, `best_overall_ev` | **0–1 fraction** | `0.0617` is a **+6.17%** edge |
| `kelly_*`, `best_overall_kelly` | **0–1 fraction** | `0.0049` stakes 0.49% of bankroll |
| `high_confidence_threshold` | 0–1 fraction | `0.7` |
| `roi_pct`, `hit_rate_pct` (on `/stats`) | **already 0–100** | `45.0` is +45% |

So EV must be multiplied by 100 before display, and ROI must not be. Formatting
`ev_away: 0.0617` as though it were already a percentage renders "0.06%" for
what is actually a +6.17% edge.

### Timestamps have no timezone suffix

`commence_time` and `synced_at` are real UTC instants, but they serialise
without a `Z` — `"2026-08-19T19:00:00"`, not `"2026-08-19T19:00:00Z"`.
JavaScript reads an offsetless date-time as **local** time, so a client that
parses these as-is shows every viewer outside UTC the wrong kick-off. Append the
suffix before parsing.

`match_date` and `source_date` are the opposite problem: bare calendar dates
already resolved to `STATPITCH_TIMEZONE`. Parsing `"2026-08-19"` with a
date-time constructor lands on UTC midnight, which renders as the 18th for any
viewer behind UTC — including America/Managua, the zone it was resolved in.

### Only the over lines carry a probability

The model publishes `over_1_5`, `over_2_5` and `over_3_5` and no unders: the
under probability is the complement, `1 - over_x`. The unders **do** have their
own `odds_under_*`, `ev_under_*` and `kelly_under_*`, so an under row is a
derived probability against a real quoted price. There is no `under_1_5` field
to read, and asking for one is the fastest way to a crash.

### The two pick groups are not symmetric

`best_bet` is the best 1X2 selection and carries `best_bet_odds` and
`best_bet_prob` — **no EV and no Kelly**. `best_overall_bet` is the best pick
across all eleven markets and carries `best_overall_odds`, `best_overall_prob`,
`best_overall_ev` and `best_overall_kelly`. They correspond to the two ledger
bases in that order.

### `odds_coverage` says whether an odds event matched at all

A boolean, and the honest way to ask "is this fixture priced". It is not the
same as "every market has a price". StatPitch publishes `market_families:
["1x2"]` and nothing else, so a fixture routinely has real 1X2 odds and null for
all eight goals and BTTS markets — those have had no price source since The Odds
API stopped being asked for markets.

One consequence worth knowing: with a single market priced, `best_bet` and
`best_overall_bet` select the same row every time, so the `1x2` and `overall`
ROI series read identically. The gap between them is not evidence of anything
until totals ship upstream.

### A priced fixture can still produce no bet

`kelly_*` is null when the edge failed to clear the minimum fractional Kelly —
including on a market with a **positive** EV. A live fixture carried
`ev_away: 0.0617` with `kelly_away: null` and `best_overall_bet: null`. So there
are three distinct "no bet" states, and they mean different things:

| State | Meaning |
|---|---|
| `odds_coverage: false` | No odds event matched; nothing to bet into |
| priced, `ev <= 0` | A price exists and the model sees no edge |
| priced, `ev > 0`, `kelly` null | There is an edge, but too small to be worth the variance |

### `explanation` is a feature attribution, not prose

An object with `units` (a sentence describing what the numbers mean) and `home`
and `away` arrays of per-feature contributions:

```json
{
  "units": "Contributions are additive in log goal-rate and multiplicative on goals: ...",
  "home": [
    { "feature": "elo_diff", "feature_value": 259.05, "contribution": 0.2871, "multiplier": 1.3326 },
    { "feature": "other",    "feature_value": null,   "contribution": 0.0701, "multiplier": 1.0727 }
  ],
  "away": [ ... ]
}
```

`contribution` is additive in log goal-rate; `multiplier` is `e^contribution`.
The `other` row aggregates the remainder and has a null `feature_value`.
Features seen in production include `elo_diff`, `home_elo`, `away_elo`,
`home_rest_days`, `away_rest_days`, `home_venue_scored_10`,
`home_venue_conceded_10`, `h2h_matches` and `away_matches_played`. Treat the
list as open — it comes from the model, not from a fixed enum.

### `correct_scores` is a top-10 scoreline distribution

```json
[{ "home": 2, "away": 0, "probability": 0.1211 }, { "home": 1, "away": 0, "probability": 0.0972 }]
```

Ten entries, descending by probability, summing to well under 1 — the tail is
not included.

### Fields worth understanding before you render anything

**`date_confirmed`** — `false` means the date is a **matchday placeholder**, not
a real kickoff. Upstream, roughly 88% of the fixture list sits on one. Render
these as "date TBC" or "week of...", never as a specific day. `kickoff` is null
whenever this is false.

**`commence_time` vs `kickoff`** — `kickoff` is a bare `"19:00"` with no
timezone and cannot be converted to a local day. `commence_time` is a real UTC
instant, taken from `kickoff_utc` on StatPitch's `/odds/matchday`, and is what
`match_date` is derived from. It is null for a fixture that endpoint has not
published yet, and `match_date` then falls back to StatPitch's nominal
`source_date`.

**`selections`** — Pro and above. StatPitch's own priced rows for this fixture,
one per outcome, and the fullest form of "Book vs ML". The four prices are
deliberately separate and must not be collapsed into one:

| Field | What it is |
|---|---|
| `odds` | The best quote. **The only bettable number of the four** |
| `reference_odds` | The benchmark book the rule measured against |
| `consensus_odds` | The panel mean |
| `fair_odds` | That consensus, de-vigged |

`stake_fraction` separates analysis from recommendation: everything in the list
has been priced and graded, and only rows above zero are picks. `reasons` says
why a row was refused, in readable prose. The list is empty rather than absent
on an unpriced fixture, which is normal days ahead of kickoff.

Note `model_edge` is `0.0` on every row today, and `p_used` equals `q_fair`: the
market-shrinkage weight fits at 0.000, so selections come from a *price*
disagreement between a book and the benchmark, not from the model out-predicting
the market. **Do not label one a "model pick".**

**`fully_rated`** — `false` means at least one club had no measured Elo and fell
back to a prior. The number is still well formed, but it is a much weaker claim.
`home_elo_source` / `away_elo_source` say which tier of evidence was used
(`club_elo`, `entrant_prior`, `pooled_prior`, `default`).

**`prediction_source`** — `fitted_goal_model` is the trained model.
`elo-poisson` is the measurably weaker fallback (+0.0064 log-loss), and appears
for fixtures that missed the last precompute run. Worth surfacing.

**`actual_result`** — null until the fixture settles, and its value set is
**not published in the schema** (it is a bare `string | null`). Settle a pick
from `home_score` and `away_score` instead; goals are unambiguous.

**`home_crest_url` / `away_crest_url`** — currently **always null**. StatPitch
supplies no crest, and the old country-flag URLs became meaningless once the
domain moved from national teams to clubs. The fields exist so a crest source
can be added later without a schema change.

### Unpriced fixtures are normal

When no odds event matched, or no API key is configured, `odds_coverage` is
`false` and every price, EV, Kelly and pick field is null while the prediction
stays fully populated. Show the prediction, hide the betting UI. It is not an
error, and it is the common case for the seven unpriced competitions.

---

## 4. Endpoints

All under `/statpitch`. `GET` is public; the sync needs `X-API-KEY`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/fixtures` | The whole window. Sets `X-Total-Count` |
| `GET` | `/fixtures/window` | The three live dates |
| `GET` | `/fixtures/yesterday` \| `/today` \| `/tomorrow` | One day each |
| `GET` | `/fixtures/today/best` | Highest win probability |
| `GET` | `/fixtures/today/value-bets` | Positive edge, strongest stake first |
| `GET` | `/bets/today` | StatPitch's own pick, with its caveats |
| `GET` | `/fixtures/{id}` | By numeric primary key |
| `GET` | `/stats` | Today's shape plus rolling ROI |
| `GET` | `/ledger` | The permanent record, paginated |
| `POST` | `/sync` | Locked. The daily pass |

### `GET /competitions`

Fifteen rows, ungated. Three independent booleans, because they answer three
different questions:

| flag | true when |
|---|---|
| `free_tier` | a free account can see it |
| `priced` | StatPitch publishes a market for it |
| `stakeable` | the selection rule is measured to earn there |

`stakeable` is the one to render. It is the only way to answer "why does this
league never have picks" — an empty slate returns the same reason code whether a
competition is outside the rule's scope or merely had a quiet day, so a frontend
inferring scope from absence will report a working product as broken.

It is read from the most recently synced day's `selection_rule_competitions`,
not from a constant, because the scope is re-measured upstream and moves. A
database that has never synced falls back to `STAKEABLE_LEAGUES` in
`leagues.py`; nothing else should read that constant.

### `GET /fixtures`

| Parameter | Type | Notes |
|---|---|---|
| `day` | `yesterday` \| `today` \| `tomorrow` | Restrict to one day |
| `competition_id` | string | Exact match |
| `value_bets_only` | bool | Only fixtures with a qualifying pick |

Returns the window ordered by date, then kickoff. Sets `X-Total-Count`. The
three day-specific endpoints do **not** set that header — they are already a
complete day.

### `GET /fixtures/today/value-bets`

| Parameter | Type | Default |
|---|---|---|
| `basis` | `overall` \| `1x2` \| `rule` | `overall` |

`overall` and `1x2` are **our** selections: fixtures whose best pick clears the
minimum fractional Kelly, ordered by Kelly descending. Ranking on Kelly rather
than EV is deliberate — EV alone cannot tell a sound bet from a lottery ticket,
since a 5% shot at 25.0 carries +25% EV and a stake far too small to be worth
the variance.

`rule` is **StatPitch's** own selection rule instead, ordered by the stake it
assigned. It is not the default and will not become one: this endpoint has been
measuring our Kelly selections since it existed, and repointing it would rewrite
what its numbers have always meant. An unknown `basis` is a 422.

### `GET /bets/today`

StatPitch's own daily pick, served from cache rather than proxied — the upstream
instance sleeps after fifteen minutes idle, and a proxied read would drop the
caveats at exactly the moment somebody is looking at a bet.

**`caveat` is never null while `bets` is non-empty, and has to be rendered.**
The rule behind these picks carries five seasons of measured closing-line value
(+0.51%, t=7.53, 7,790 matches) but runs on a 25-book panel that has none, so
its calibration is inherited rather than re-measured. A pick shown without that
statement claims more than the evidence supports. If the upstream string is
somehow missing, one is built from the rule status stored on the pick itself, so
there is no path where a recommendation arrives unqualified.

An empty day is a `200`, not a `404`. The rule fires only where a book misprices
against its benchmark, which on most days is nowhere at all — `reason` and
`empty_because` say which. Note that `empty_because.cause` is frequently
`fixtures_today_carry_no_price`: prices publish per matchday block, so a day can
have fixtures and no card.

Pro and above. A staked recommendation *is* the edge indicator, so there is no
reduced free version worth returning.

### `GET /ledger`

| Parameter | Type | Default |
|---|---|---|
| `basis` | `1x2` \| `overall` \| `rule` | both |
| `competition_id` | string | all |
| `offset` | int >= 0 | `0` |
| `limit` | int 1-100 | `10` |

Newest first. Sets `X-Total-Count`. An unknown `basis` is a 422, not an empty
list — it is a typo, not a query with no results. The same is true of an unknown
`day` on `/fixtures`, which is a three-value enum.

```json
{
  "id": 2,
  "fixture_id": "ESP.LALIGA|2026-2027|FC Barcelona|Athletic Club",
  "competition_id": "ESP.LALIGA",
  "home_team": "FC Barcelona",
  "away_team": "Athletic Club",
  "match_date": "2026-08-17",
  "settled_at": "2026-08-18T07:03:12.869827",
  "basis": "overall",
  "selection": "over_2_5",
  "probability": 0.534,
  "odds_taken": 1.72,
  "stake_units": 1.0,
  "kelly_fraction": 0.05,
  "won": true,
  "pnl_units": 0.72,
  "home_score": 3,
  "away_score": 1,
  "model_version": "goals-20260813-bb07c99e"
}
```

### Empty is not an error

Collection endpoints return `[]` with a 200 when a day is empty. With most dates
provisional upstream, a real matchday can legitimately show nothing filed under
today. Only two endpoints 404 — `/fixtures/today/best` and `/fixtures/{id}` —
and both promise a single resource.

---

## 5. Performance and the ledger

`GET /statpitch/stats` is the stats bar.

```json
{
  "generated_for": "2026-08-18",
  "timezone": "America/Managua",
  "window": { "yesterday": "2026-08-17", "today": "2026-08-18", "tomorrow": "2026-08-19" },
  "fixtures_today": 1,
  "fixtures_tomorrow": 1,
  "date_confirmed_today": 1,
  "high_confidence_today": 0,
  "high_confidence_threshold": 0.7,
  "value_bets_today": 1,
  "rule_bets_today": 1,
  "roi": [
    {
      "basis": "1x2",
      "week":  { "bets": 1, "wins": 1, "staked_units": 1.0, "returned_units": 1.45,
                 "pnl_units": 0.45, "roi_pct": 45.0, "hit_rate_pct": 100.0 },
      "month": { "bets": 1, "wins": 1, "staked_units": 1.0, "returned_units": 1.45,
                 "pnl_units": 0.45, "roi_pct": 45.0, "hit_rate_pct": 100.0 }
    },
    { "basis": "overall", "week": {}, "month": {} },
    { "basis": "rule", "week": {}, "month": {} }
  ]
}
```

`value_bets_today` counts our Kelly picks; `rule_bets_today` counts StatPitch's
staked selections. A fixture can easily carry one and not the other.

### Three series, never averaged

`roi` always has exactly three entries, and they measure **different
strategies**:

| basis | whose selection | what it bets |
|---|---|---|
| `1x2` | Ours | The best home/draw/away pick only |
| `overall` | Ours | The best pick across 1X2, over/under and BTTS |
| `rule` | StatPitch's | Its own selection rule, at its own price and probability |

The first two are kept apart so you can see whether the multi-market Kelly
filter actually beats plain 1X2. The third is kept apart from both because it is
not our selection at all: it is measured at StatPitch's `p_used` and its own
quote, and scoring it against our inputs would measure neither system.
Averaging any of them would answer none of the three questions.

Two things to know about reading these today:

- **`1x2` and `overall` currently agree.** Only the 1X2 family carries a price,
  so the across-markets pick and the confined one are the same row every time.
  Their two figures will read identically until totals ship upstream, and the
  gap between them is not evidence of anything meanwhile.
- **`rule` accrues slowly.** At most three bets a day across all competitions,
  and most days none — so expect long stretches where its window is `null`.

### What the numbers mean

- **Windows are rolling and inclusive.** `week` is today and the six days before
  it; `month` is today and the previous 29. Both are measured against
  `match_date`, not settlement time, so a late-recorded result still lands in
  the week it was played.
- **ROI is flat-stake.** One unit per bet, so `roi_pct` reads as return per unit
  staked. Note that `roi_pct` and `hit_rate_pct` are the only rates in this API
  already on a 0–100 scale — everything on a fixture is a 0–1 fraction. `kelly_fraction` is stored on every row, so a stake-weighted variant
  can be derived later without rewriting settled history.
- **`roi_pct` and `hit_rate_pct` are `null`, not `0.0`, when nothing settled.**
  An empty window has no ROI, and rendering it as break-even would claim a
  result that was never measured. Show the null state as "no bets settled yet".
- **`pnl_units`** is `odds - 1` on a winner and `-1` on a loser. A ledger row
  exists only where a bet was actually placed, so a fixture with no qualifying
  pick contributes nothing to either series.

`high_confidence_today` counts fixtures where the home or away probability is at
least `high_confidence_threshold`. The draw is excluded on purpose — a likely
draw is not a confident match.

---

## 6. The sync

`POST /statpitch/sync`, with `X-API-KEY`. One pass does everything, in an order
that matters:

```
fetch fixtures -> price them -> settle finished ones -> bank the ledger -> prune
```

Banking before pruning is what keeps a result from being lost to retention.

It is **idempotent**. Running it twice changes nothing, and a failed run is
corrected by the next one rather than by hand. A fixture already banked is never
re-priced, so a settled bet cannot be silently rewritten.

```json
{
  "window": { "yesterday": "2026-08-17", "today": "2026-08-18", "tomorrow": "2026-08-19" },
  "fetched": 7,
  "stored": 7,
  "priced": 5,
  "unpriced": 2,
  "selections": 84,
  "rule_bets": 1,
  "settled": 3,
  "ledgered": 4,
  "pruned": 2,
  "model_version": "goals-20260813-bb07c99e",
  "warnings": []
}
```

`warnings` is the field to actually read. It reports unpriced competitions,
elo-poisson fallbacks, quota problems and abandoned fixtures without failing the
run.

### Scheduling

Runs at **06:00 UTC**, which is local midnight, from
`.github/workflows/statpitch-sync.yml`. It needs two repository secrets:
`API_BASE_URL` and `API_MASTER_KEY`.

It lives in GitHub Actions rather than `vercel.json` because Vercel Cron issues
`GET` requests and cannot attach a custom header, so it could not present the
API key. Two caveats: GitHub delays scheduled runs under load, and disables
schedules after 60 days without repository activity. The sync being idempotent
is what makes a late or missed run harmless.

There is no in-process scheduler. The app runs serverless, where background
threads do not survive between requests.

---

## 7. Configuration

Everything is optional. Without it the StatPitch routes still serve, they just
have less to serve.

| Variable | Default | Notes |
|---|---|---|
| `STATPITCH_BASE_URL` | `https://statpitch-api.onrender.com` | |
| `STATPITCH_TIMEOUT_SECONDS` | `60` | The free instance sleeps; the first call pays a cold start of tens of seconds |
| `STATPITCH_COMPETITIONS` | the eight priced leagues | Comma-separated |
| `STATPITCH_TIMEZONE` | `America/Managua` | Any IANA zone, validated at boot |
| `STATPITCH_RETENTION_DAYS` | `1` | Days kept either side of today |
| `CORS_ORIGINS` | localhost `5173`–`5175`, localhost `8000`, `gabrielmayorga.dev`, `www.gabrielmayorga.dev` | Comma-separated or a JSON list |

### CORS

Browser clients must call from an allow-listed origin. `X-Total-Count` is in
`expose_headers`, so `fetch`/`axios` can read it cross-origin — without that it
would be invisible to JavaScript and pagination would read `undefined`.

A missing `Access-Control-Allow-Origin` almost always means the caller's origin
is not on the list, not that CORS is off. Vite falls back to `5174` when `5173`
is taken, which is the usual cause locally. Note that `curl` returns no
`Access-Control-Allow-Origin` unless you pass `-H "Origin: ..."` — that is
correct behaviour and not evidence of a fault.

### Quota

There is none. **This API holds no third-party credential at all.**

Prices come from StatPitch, keyed by `fixture_id` and free to us. Scores come
from ESPN's scoreboard, which is keyless and unmetered — one request per
competition per run, covering the whole lookback as a date range.

The Odds API is gone from this codebase entirely. Its 500/month allowance is
spent by the model API on the 25-book price panel behind the card, and asking it
for scores here would have made prices and results compete for one budget.
Prices are the half with no free substitute; results are the half ESPN gives
away.

---

## 8. Operations

### Reading a sync that looks wrong

| Symptom | Likely cause |
|---|---|
| `fetched` high, `priced` 0 | StatPitch has not published this matchday block yet — check `warnings` |
| `unpriced` high | Normal days ahead of kickoff: the price feed publishes per matchday block |
| `settled` stuck at 0 | Club names failed to join a score, or ESPN is unreachable — check `warnings` |
| `ledgered` 0 while `settled` rises | Correct when the settled fixtures carried no pick. A fixture with no price owes the ledger nothing |
| `settled` 0 with finished matches | Scores lag; the next run picks them up |
| `pruned` 0 with old fixtures | Correct — they are unbanked and being protected |
| ROI null after weeks | Nothing ever priced, so no bet was ever placed |

### Club name matching

**Results only.** Prices arrive from StatPitch keyed by `fixture_id`, so the
whole class of mismatch this guards against cannot happen to a price any more.
It can still happen to a score, which is why the module has not gone anywhere.

StatPitch uses full registered names, ESPN short trading names. The join
normalises both (accents, corporate prefixes, founding years) and then scores
the **pair**.

This got easier when scores moved to ESPN. `statpitch_team` was populated from
ESPN during the crest backfill and `_ALIASES` is tuned for ESPN's vocabulary —
the Köln entry exists because ESPN calls them Cologne — so the join now runs
against the names the alias table was built for. Matching one name at a time is unsafe: `RCD Espanyol de Barcelona`
resembles `Barcelona` about as much as it resembles `Espanyol`, and only the
away side breaks the tie.

When no candidate clears the threshold the fixture stays **unsettled** rather
than matched to a guess. That is deliberate — a wrong match would attach another
match's scoreline to a bet and corrupt the ledger permanently.

### Status codes

| Status | When |
|---|---|
| `200` | Success, including an empty day |
| `401` / `403` | Missing or wrong `X-API-KEY` on the sync |
| `404` | `/fixtures/{id}` or `/fixtures/today/best` with nothing to return |
| `422` | Unknown `basis` on the ledger, or an unknown `day` on `/fixtures` — a typo, not a query with no results |
| `502` | StatPitch unreachable, or refused with a reason code |

A StatPitch refusal is a 200 upstream but a **502 here**: `NO_FIXTURE_SOURCE`
means its fixture artifact failed to load, which is a broken deploy rather than
a quiet day, and returning an empty window would hide that.

### Migrations

`alembic upgrade head` from an **empty** database does not work in this repo,
and did not before this feature: revision `9c1ad9f14faf` is an empty stamp, and
the original schema was created by `create_all()` out of band. The StatPitch
migration guards its table drop so it runs against both a deployed database and
a fresh one, but the chain as a whole is still not reproducible from scratch.
