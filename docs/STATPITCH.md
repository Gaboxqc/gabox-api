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

| Needed | Source | Why not StatPitch |
|---|---|---|
| A selection | Ours — EV and quarter-Kelly | It refuses to pick one |
| A real price | The Odds API | Its `fair_odds` are no-vig and unbettable |
| A final score | The Odds API `/scores` | It has no results endpoint |

**The predictions are StatPitch's. The bets are ours.** Every selection, stake
and ROI figure in this API is computed here, from StatPitch's probabilities and
a real bookmaker price. Judge the track record accordingly.

**Fixtures are temporary, the record is permanent.** Two tables, two lifetimes —
see [The three-day window](#2-the-three-day-window).

**Five competitions are priced.** StatPitch covers twelve; only five have an
odds market we can price against, and each one costs API quota. The rest can be
synced for predictions but will never produce a bet.

| competition_id | name | priced by default |
|---|---|---|
| `ENG.PL` | Premier League | yes |
| `ESP.LALIGA` | La Liga | yes |
| `GER.BUNDESLIGA` | Bundesliga | yes |
| `ITA.SERIEA` | Serie A | yes |
| `FRA.LIGUE1` | Ligue 1 | yes |
| `ENG.FA_CUP`, `GER.DFB_POKAL`, `ITA.COPPA_ITALIA`, `UEFA.UCL`, `UEFA.UEL` | cups | no, but a sport key exists |
| `ESP.COPA_DEL_REY`, `FRA.COUPE_DE_FRANCE` | cups | no sport key at all |

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
same as "every market has a price": with the default `ODDS_API_MARKETS=h2h` a
fixture can have `odds_coverage: true`, real 1X2 odds, and null for all eight
goals and BTTS markets.

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
instant from the odds feed, and is what `match_date` is derived from. It is null
when the fixture could not be matched to an odds event, and `match_date` then
falls back to StatPitch's nominal `source_date`.

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
| `GET` | `/fixtures/today/value-bets` | Positive edge, best Kelly first |
| `GET` | `/fixtures/{id}` | By numeric primary key |
| `GET` | `/stats` | Today's shape plus rolling ROI |
| `GET` | `/ledger` | The permanent record, paginated |
| `POST` | `/sync` | Locked. The daily pass |

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

Only fixtures whose best selection clears the minimum fractional Kelly, ordered
by Kelly descending. Ranking on Kelly rather than EV is deliberate: EV alone
cannot tell a sound bet from a lottery ticket, since a 5% shot at 25.0 carries
+25% EV and a stake far too small to be worth the variance.

### `GET /ledger`

| Parameter | Type | Default |
|---|---|---|
| `basis` | `1x2` \| `overall` | both |
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
  "roi": [
    {
      "basis": "1x2",
      "week":  { "bets": 1, "wins": 1, "staked_units": 1.0, "returned_units": 1.45,
                 "pnl_units": 0.45, "roi_pct": 45.0, "hit_rate_pct": 100.0 },
      "month": { "bets": 1, "wins": 1, "staked_units": 1.0, "returned_units": 1.45,
                 "pnl_units": 0.45, "roi_pct": 45.0, "hit_rate_pct": 100.0 }
    },
    { "basis": "overall", "week": {}, "month": {} }
  ]
}
```

### Two series, never averaged

`roi` always has exactly two entries, and they measure **different strategies**:

| basis | what it bets |
|---|---|
| `1x2` | The best home/draw/away pick only |
| `overall` | The best pick across 1X2, over/under and BTTS |

They are kept apart so you can see whether the multi-market Kelly filter
actually beats plain 1X2. Averaging them would answer neither question.

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
  "unmatched_odds": 2,
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
| `STATPITCH_COMPETITIONS` | the five priced leagues | Comma-separated |
| `STATPITCH_TIMEZONE` | `America/Managua` | Any IANA zone, validated at boot |
| `STATPITCH_RETENTION_DAYS` | `1` | Days kept either side of today |
| `ODDS_API_KEY` | none | Without it, predictions store but never price |
| `ODDS_API_REGION` | `eu` | |
| `ODDS_API_MARKETS` | `h2h` | See quota below |
| `ODDS_API_BOOKMAKERS` | all | Comma-separated to restrict |
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

The Odds API costs **one request per market per league per run**. Against a
500/month free tier:

| Markets | 5 leagues, daily | Verdict |
|---|---|---|
| `h2h` | ~150/month | comfortable |
| `h2h,totals` | ~300/month | workable |
| `h2h,totals,btts` | ~450/month | over budget once scores are counted |

Scores cost one request per league per run on top. `h2h` alone is the default
for that reason. Widening `ODDS_API_MARKETS` enables the over/under and BTTS
markets in the `overall` series, but needs a paid tier to be sustainable.

---

## 8. Operations

### Reading a sync that looks wrong

| Symptom | Likely cause |
|---|---|
| `fetched` high, `priced` 0 | `ODDS_API_KEY` missing or quota exhausted — check `warnings` |
| `unmatched_odds` high | Club names failed to join; see `matching.py` |
| `settled` 0 with finished matches | Scores lag; the next run picks them up |
| `pruned` 0 with old fixtures | Correct — they are unbanked and being protected |
| ROI null after weeks | Nothing ever priced, so no bet was ever placed |

### Club name matching

StatPitch uses full registered names, The Odds API short trading names. The join
normalises both (accents, corporate prefixes, founding years) and then scores
the **pair**. Matching one name at a time is unsafe: `RCD Espanyol de Barcelona`
resembles `Barcelona` about as much as it resembles `Espanyol`, and only the
away side breaks the tie.

When no candidate clears the threshold the fixture is stored **unpriced** rather
than matched to a guess. That is deliberate — a wrong match would attach another
club's odds to a prediction and corrupt the ledger permanently.

### Status codes

| Status | When |
|---|---|
| `200` | Success, including an empty day |
| `401` / `403` | Missing or wrong `X-API-KEY` on the sync |
| `404` | `/fixtures/{id}` or `/fixtures/today/best` with nothing to return |
| `422` | Unknown `basis` on the ledger, or an unknown `day` on `/fixtures` — a typo, not a query with no results |
| `502` | StatPitch unreachable, or refused with a reason code |
| `503` | The Odds API key is missing, or its quota is exhausted |

A StatPitch refusal is a 200 upstream but a **502 here**: `NO_FIXTURE_SOURCE`
means its fixture artifact failed to load, which is a broken deploy rather than
a quiet day, and returning an empty window would hide that.

### Migrations

`alembic upgrade head` from an **empty** database does not work in this repo,
and did not before this feature: revision `9c1ad9f14faf` is an empty stamp, and
the original schema was created by `create_all()` out of band. The StatPitch
migration guards its table drop so it runs against both a deployed database and
a fresh one, but the chain as a whole is still not reproducible from scratch.
