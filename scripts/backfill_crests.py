"""Fill the club registry's crests from ESPN, via Cloudflare R2.

    python -m scripts.backfill_crests --dry-run     # report, change nothing
    python -m scripts.backfill_crests               # resolve and upload
    python -m scripts.backfill_crests --only arsenal
    python -m scripts.backfill_crests --refresh     # re-resolve clubs that have one

Run it after a sync has populated `statpitch_team`, and again whenever the
report shows clubs without a crest. It is safe to re-run: keys contain a hash of
the image bytes, so an unchanged crest resolves to a key that already exists and
nothing is uploaded.

Requires the optional extra:

    pip install -e ".[crests]"

Deliberately a command-line tool rather than an API route. It reaches out to a
third party, decodes images and writes to object storage — none of which belongs
in a serverless function with a request waiting on it.

**Matching is allowed to fail.** A club whose crest cannot be resolved
unambiguously is reported and left alone, because the wrong badge is materially
worse than none: a monogram reads as "not loaded yet", Barcelona's badge on an
Espanyol fixture reads as a broken product. The report lists exactly what needs
a manual alias.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlmodel import Session, select

from api.core.database import engine
from api.statpitch.crests import (
    CREST_SIZES,
    DEFAULT_CREST_SIZE,
    EspnTeam,
    describe_failure,
    fetch_espn_teams,
    normalise_crest,
    resolve_crest,
)
from api.statpitch.leagues import ESPN_LEAGUE_SLUGS
from api.statpitch.storage import StorageUnavailable, crest_key, is_configured, put_crest
from api.statpitch.teams import StatPitchTeam

log = logging.getLogger("backfill_crests")

# ESPN is fine with this pace and we make twelve calls, once. Being polite to an
# undocumented endpoint costs nothing here.
_HTTP_TIMEOUT = 30.0


@dataclass
class Report:
    resolved: int = 0
    uploaded: int = 0
    skipped: int = 0
    unresolved: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "",
            f"  resolved   {self.resolved}",
            f"  uploaded   {self.uploaded}",
            f"  unchanged  {self.skipped}",
            f"  unresolved {len(self.unresolved)}",
        ]
        if self.unresolved:
            lines.append("")
            lines.append("  Needs a manual alias or a crest from elsewhere:")
            lines.extend(f"    - {reason}" for reason in self.unresolved)
        return "\n".join(lines)


async def _load_espn() -> tuple[dict[str, list[EspnTeam]], list[EspnTeam]]:
    """Every club ESPN lists, per competition and pooled.

    The pooled list backs the fallback pass: ESPN's per-league roster is not
    reliably current — `eng.1` has returned Coventry and Leeds but neither
    Wolves nor West Ham — and a club missing from its own league is usually
    present in a cup or European list.
    """
    by_competition: dict[str, list[EspnTeam]] = {}
    pooled: list[EspnTeam] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for competition_id, slug in ESPN_LEAGUE_SLUGS.items():
            try:
                teams = await fetch_espn_teams(client, slug)
            except httpx.HTTPError as exc:
                log.warning("Could not read %s (%s): %s", competition_id, slug, exc)
                continue

            by_competition[competition_id] = teams
            for team in teams:
                if team.espn_id not in seen:
                    seen.add(team.espn_id)
                    pooled.append(team)

            print(f"  {competition_id:22} {len(teams):>4} clubs")

    return by_competition, pooled


async def _download(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        log.warning("Could not download %s: %s", url, exc)
        return None


async def _store_crest(
    client: httpx.AsyncClient,
    team: StatPitchTeam,
    source_url: str,
    *,
    dry_run: bool,
    force: bool = False,
) -> tuple[str | None, str | None, bool]:
    """Fetch, normalise and upload. Returns `(url, key, uploaded_anything)`.

    Every size is keyed on a hash of the *source*, so they share one name and
    differ only in the suffix — a caller holding any of them can reach the
    others. `DEFAULT_CREST_SIZE` is what gets recorded on the team row.
    """
    raw = await _download(client, source_url)
    if raw is None:
        return None, None, False

    primary_url: str | None = None
    primary_key: str | None = None
    uploaded = False

    for size in CREST_SIZES:
        payload = normalise_crest(raw, size)
        key = crest_key(team.slug, raw, size)

        if dry_run:
            url = f"(dry-run) {key}"
        else:
            url, created = put_crest(key, payload, force=force)
            uploaded = uploaded or created

        if size == DEFAULT_CREST_SIZE:
            primary_url, primary_key = url, key

    return primary_url, primary_key, uploaded


async def run(only: str | None, refresh: bool, dry_run: bool) -> Report:
    report = Report()

    print("Reading ESPN team lists:")
    by_competition, pooled = await _load_espn()
    if not pooled:
        print("\nESPN returned nothing at all; aborting rather than blanking crests.")
        return report

    with Session(engine) as db:
        statement = select(StatPitchTeam)
        if only:
            statement = statement.where(StatPitchTeam.slug == only)
        elif not refresh:
            statement = statement.where(StatPitchTeam.crest_url.is_(None))
        clubs = db.exec(statement).all()

        if not clubs:
            print("\nNothing to do — every club in the registry already has a crest.")
            return report

        print(f"\nResolving {len(clubs)} club(s):")

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            for team in clubs:
                candidates = by_competition.get(team.competition_id, [])
                match = resolve_crest(team.display_name, candidates, pooled)

                if match is None:
                    report.unresolved.append(
                        describe_failure(team.display_name, candidates or pooled)
                    )
                    print(f"  --   {team.display_name:32} unresolved")
                    continue

                source_url = match.team.best_logo_url
                if source_url is None:
                    report.unresolved.append(f"{team.display_name}: matched but ESPN has no badge")
                    print(f"  --   {team.display_name:32} matched, no badge")
                    continue

                url, key, uploaded = await _store_crest(
                    client, team, source_url, dry_run=dry_run, force=refresh
                )
                if url is None:
                    report.unresolved.append(f"{team.display_name}: download failed")
                    continue

                report.resolved += 1
                if uploaded:
                    report.uploaded += 1
                else:
                    report.skipped += 1

                print(
                    f"  ok   {team.display_name:32} -> {match.team.display_name:26} "
                    f"{match.score:.2f} (+{match.margin:.2f})"
                )

                if dry_run:
                    continue

                team.crest_url = url
                team.crest_key = key
                team.crest_source = "espn"
                team.crest_updated_at = datetime.now(UTC)
                # ESPN's name is usually the nicer one to show.
                team.display_name = match.team.display_name or team.display_name
                db.add(team)

            if not dry_run:
                db.commit()

    return report


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Fill club crests from ESPN into R2.")
    parser.add_argument("--only", help="Just this one club, by registry slug.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-resolve clubs that already have a crest, not only the empty ones.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen. Touches neither R2 nor the database.",
    )
    args = parser.parse_args()

    if not args.dry_run and not is_configured():
        print(
            "R2 is not configured. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY and R2_BUCKET, or pass --dry-run.",
            file=sys.stderr,
        )
        return 1

    try:
        report = asyncio.run(run(args.only, args.refresh, args.dry_run))
    except StorageUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(report.render())
    # Unresolved clubs are a normal outcome, not a failure: the UI falls back to
    # a monogram and the report says which ones need a human. Exiting non-zero
    # would make this unusable from CI.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
