"""Crest resolution: parsing ESPN, matching one club, and where bytes land.

The matching tests are the ones that matter. Attaching the wrong badge is a
worse outcome than attaching none, so most of these pin down the *refusals*.

No network: ESPN is represented by the shape it actually returns. Pillow and
boto3 are optional extras the API does not ship, so the tests that need them
skip rather than fail.
"""

import pytest

from api.statpitch import storage
from api.statpitch.crests import (
    MIN_CREST_SIMILARITY,
    EspnTeam,
    _parse_teams,
    describe_failure,
    match_team,
    normalise_crest,
    resolve_crest,
    score_team,
)


def _espn_payload(*teams: dict) -> dict:
    """ESPN's envelope: sports -> leagues -> teams -> team."""
    return {"sports": [{"leagues": [{"teams": [{"team": team} for team in teams]}]}]}


def _team(name: str, short: str = "", abbreviation: str = "", team_id: str = "1") -> EspnTeam:
    return EspnTeam(
        espn_id=team_id,
        display_name=name,
        short_name=short,
        abbreviation=abbreviation,
        logo_url=f"https://a.espncdn.com/i/teamlogos/soccer/500/{team_id}.png",
        dark_logo_url=f"https://a.espncdn.com/i/teamlogos/soccer/500-dark/{team_id}.png",
    )


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_a_club_is_read_with_all_three_names():
    payload = _espn_payload(
        {
            "id": "359",
            "displayName": "Arsenal",
            "shortDisplayName": "Arsenal",
            "abbreviation": "ARS",
            "logos": [{"href": "https://a.espncdn.com/i/teamlogos/soccer/500/359.png"}],
        }
    )

    (team,) = _parse_teams(payload)
    assert team.espn_id == "359"
    assert team.names == ("Arsenal", "Arsenal", "ARS")


def test_the_dark_variant_is_told_apart_from_the_light_one():
    """The UI is near-black, so the dark badge is the one worth having."""
    payload = _espn_payload(
        {
            "id": "359",
            "displayName": "Arsenal",
            "logos": [
                {"href": "https://a.espncdn.com/i/teamlogos/soccer/500/359.png"},
                {"href": "https://a.espncdn.com/i/teamlogos/soccer/500-dark/359.png"},
            ],
        }
    )

    (team,) = _parse_teams(payload)
    assert team.logo_url.endswith("/500/359.png")
    assert team.dark_logo_url.endswith("/500-dark/359.png")
    assert team.best_logo_url == team.dark_logo_url


def test_a_club_with_only_a_light_badge_still_resolves():
    payload = _espn_payload(
        {
            "id": "1",
            "displayName": "Somebody",
            "logos": [{"href": "https://a.espncdn.com/i/teamlogos/soccer/500/1.png"}],
        }
    )

    (team,) = _parse_teams(payload)
    assert team.dark_logo_url is None
    assert team.best_logo_url.endswith("/500/1.png")


def test_a_club_with_no_badge_is_kept_but_has_nothing_to_download():
    """The cups are full of these — amateur sides ESPN lists without a badge."""
    (team,) = _parse_teams(_espn_payload({"id": "1", "displayName": "Some Amateur XI"}))
    assert team.best_logo_url is None


def test_an_unreadable_entry_is_skipped_rather_than_crashing_the_run():
    """Undocumented endpoint: a shape change should cost the clubs we cannot
    read, not the whole backfill."""
    payload = _espn_payload(
        {"displayName": "No identifier"},
        {"id": "2"},
        {"id": "3", "displayName": "Fine"},
    )
    assert [team.display_name for team in _parse_teams(payload)] == ["Fine"]


def test_an_empty_payload_is_not_an_error():
    assert _parse_teams({}) == []


# ── Scoring ──────────────────────────────────────────────────────────────────


def test_the_best_of_the_three_spellings_wins():
    """The registry may hold the registered name while ESPN holds the trading
    one, or the reverse."""
    candidate = _team("Athletic Club", short="Athletic", abbreviation="ATH")
    assert score_team("Athletic Club", candidate) == 1.0
    assert score_team("Athletic", candidate) == 1.0


def test_an_unrelated_name_scores_low():
    assert score_team("Real Madrid", _team("Arsenal")) < MIN_CREST_SIMILARITY


# ── Matching ─────────────────────────────────────────────────────────────────


def test_a_clear_winner_is_matched():
    candidates = [_team("Arsenal", team_id="1"), _team("Chelsea", team_id="2")]
    match = match_team("Arsenal FC", candidates)

    assert match is not None
    assert match.team.display_name == "Arsenal"
    assert match.margin > 0


def test_nothing_close_is_refused():
    assert match_team("Real Madrid", [_team("Arsenal"), _team("Chelsea")]) is None


def test_an_ambiguous_pair_is_refused():
    """A bare "Sporting" resembles Gijon and Lisbon about equally. Guessing puts
    one club's badge on the other's fixture, which is worse than no badge.

    Espanyol used to be the example here, until an alias settled it — the
    ambiguity is real, the specific clubs are not the point."""
    candidates = [_team("Sporting Gijon", team_id="1"), _team("Sporting Lisbon", team_id="2")]
    assert match_team("Sporting", candidates) is None


def test_matching_against_nothing_is_refused():
    assert match_team("Arsenal", []) is None


def test_a_lone_candidate_still_has_to_clear_the_threshold():
    """A one-club league must not hand its badge to whatever asks."""
    assert match_team("Real Madrid", [_team("Arsenal")]) is None
    assert match_team("Arsenal FC", [_team("Arsenal")]) is not None


# ── Widening the search ──────────────────────────────────────────────────────


def test_a_club_missing_from_its_own_league_is_found_elsewhere():
    """ESPN's per-league roster is not reliably current — `eng.1` has come back
    with Coventry and Leeds but no Wolves."""
    own_league = [_team("Coventry City", team_id="1")]
    everywhere = own_league + [_team("Wolverhampton Wanderers", team_id="2")]

    assert resolve_crest("Wolverhampton Wanderers", own_league) is None

    widened = resolve_crest("Wolverhampton Wanderers", own_league, everywhere)
    assert widened is not None
    assert widened.team.espn_id == "2"


def test_the_widened_pass_still_refuses_a_guess():
    """Widening trades a miss for a possible match, never for a guess."""
    everywhere = [_team("Real Madrid", team_id="1"), _team("Real Betis", team_id="2")]
    assert resolve_crest("Real", [], everywhere) is None


def test_a_hit_in_the_home_league_is_not_second_guessed():
    own_league = [_team("Arsenal", team_id="1")]
    everywhere = own_league + [_team("Arsenal", team_id="999")]

    match = resolve_crest("Arsenal", own_league, everywhere)
    assert match.team.espn_id == "1"


# ── Reporting ────────────────────────────────────────────────────────────────


def test_the_report_separates_a_miss_from_an_ambiguity():
    """They need different fixes — one a crest from elsewhere, one an alias —
    so the report has to tell them apart."""
    nothing_close = describe_failure("Real Madrid", [_team("Arsenal")])
    ambiguous = describe_failure(
        "Sporting",
        [_team("Sporting Gijon", team_id="1"), _team("Sporting Lisbon", team_id="2")],
    )

    assert "nothing close" in nothing_close
    assert "ambiguous" in ambiguous


def test_the_report_copes_with_an_empty_league():
    assert "no candidates" in describe_failure("Arsenal", [])


# ── Storage keys ─────────────────────────────────────────────────────────────


def test_the_same_bytes_always_produce_the_same_key():
    """What makes the backfill safe to re-run: unchanged bytes resolve to a key
    that already exists, and nothing is uploaded."""
    assert storage.crest_key("arsenal", b"badge", 128) == storage.crest_key(
        "arsenal", b"badge", 128
    )


def test_different_bytes_produce_a_different_key():
    """A changed crest becomes a new object rather than overwriting one the CDN
    is holding for a year."""
    assert storage.crest_key("arsenal", b"old", 128) != storage.crest_key("arsenal", b"new", 128)


def test_each_size_gets_its_own_key():
    assert storage.crest_key("arsenal", b"badge", 128) != storage.crest_key("arsenal", b"badge", 64)


def test_a_key_is_namespaced_by_club_under_the_configured_prefix():
    key = storage.crest_key("arsenal", b"badge", 128)
    assert (
        key
        == f"{storage.settings.r2_crest_prefix}/arsenal/{storage.content_hash(b'badge')}-128.webp"
    )


def test_the_prefix_keeps_crests_out_of_a_shared_bucket(monkeypatch):
    """The bucket holds other projects, so everything lands under one prefix."""
    monkeypatch.setattr(storage.settings, "r2_crest_prefix", "statpitch/crests", raising=False)
    assert storage.crest_key("arsenal", b"badge", 64).startswith("statpitch/crests/arsenal/")


def test_stray_slashes_in_the_prefix_do_not_double_up(monkeypatch):
    monkeypatch.setattr(storage.settings, "r2_crest_prefix", "/statpitch/crests/", raising=False)
    key = storage.crest_key("arsenal", b"badge", 64)

    assert key.startswith("statpitch/crests/arsenal/")
    assert "//" not in key


def test_an_empty_prefix_writes_at_the_root(monkeypatch):
    monkeypatch.setattr(storage.settings, "r2_crest_prefix", "", raising=False)
    assert storage.crest_key("arsenal", b"badge", 64).startswith("arsenal/")


def test_the_public_url_never_doubles_a_slash(monkeypatch):
    monkeypatch.setattr(
        storage.settings, "r2_public_base_url", "https://cdn.example.com/", raising=False
    )
    assert (
        storage.public_url("crests/a/b-128.webp") == "https://cdn.example.com/crests/a/b-128.webp"
    )


def test_storage_reports_itself_unconfigured_when_it_is(monkeypatch):
    """The API boots without R2 credentials, and must say so plainly rather than
    failing somewhere further in."""
    monkeypatch.setattr(storage.settings, "r2_account_id", "", raising=False)
    assert storage.is_configured() is False


# ── Image normalisation ──────────────────────────────────────────────────────


def _png(width: int, height: int) -> bytes:
    import io

    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    # A visible block inset in a transparent frame, so the border trim has
    # something to trim.
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (width // 4, height // 4, width // 2, height // 2))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_crest_comes_back_as_a_square_webp():
    Image = pytest.importorskip("PIL.Image")
    import io

    result = normalise_crest(_png(500, 300), 128)

    with Image.open(io.BytesIO(result)) as decoded:
        assert decoded.format == "WEBP"
        # Square whatever the source aspect: a wide badge and a tall one must
        # occupy the same box or a fixture list will not line up.
        assert decoded.size == (128, 128)
        assert decoded.mode in {"RGBA", "RGB"}


def test_transparency_survives():
    """Flattening onto white would put a white box on a near-black page."""
    Image = pytest.importorskip("PIL.Image")
    import io

    with Image.open(io.BytesIO(normalise_crest(_png(200, 200), 64))) as decoded:
        assert decoded.convert("RGBA").getpixel((0, 0))[3] == 0


def test_re_encoding_is_what_sanitises_the_file():
    """Whatever arrives is decoded to pixels and written back out, so trailing
    data cannot survive the trip."""
    pytest.importorskip("PIL.Image")

    clean = normalise_crest(_png(200, 200), 64)
    smuggled = normalise_crest(_png(200, 200) + b"<script>alert(1)</script>", 64)

    assert b"<script>" not in smuggled
    assert smuggled == clean


def test_a_key_never_contains_a_space():
    """Registry slugs are space-separated tokens, which object storage accepts
    and every URL built from one then carries — invalid in an href, and encoded
    differently by whichever client touches it first."""
    key = storage.crest_key("rayo vallecano madrid", b"badge", 128)

    assert " " not in key
    assert "rayo-vallecano-madrid" in key


def test_key_slugs_are_url_safe():
    assert storage.key_slug("Real Betis") == "real-betis"
    assert storage.key_slug("  inter milan  ") == "inter-milan"
    assert storage.key_slug("alavés") == "alav-s"
    assert storage.key_slug("arsenal") == "arsenal"


def test_key_slugs_do_not_start_or_end_with_a_separator():
    assert not storage.key_slug("1. fc union").startswith("-")
    assert not storage.key_slug("schalke 04 ").endswith("-")
