"""Joining StatPitch club names to The Odds API's."""

from api.statpitch.matching import (
    MIN_SIDE_SIMILARITY,
    best_match,
    normalize,
    score_pair,
    similarity,
)


class TestNormalize:
    def test_strips_accents_and_case(self):
        assert normalize("Málaga CF") == "malaga"

    def test_drops_corporate_prefixes(self):
        assert normalize("FC Barcelona") == "barcelona"
        assert normalize("Real Sociedad de Fútbol") == "real sociedad"

    def test_drops_french_club_forms(self):
        # Without these, "Stade Brestois 29" and "Stade Rennais" share a token
        # and score 0.74 against each other, while the correct "Brest" manages
        # 0.53 — which is how the crest backfill matched Brest to Rennes.
        assert normalize("Stade Brestois 29") == "brestois"
        assert normalize("Stade Rennais") == "rennais"
        assert normalize("Olympique de Marseille") == "marseille"

    def test_espanyol_resolves_rather_than_staying_ambiguous(self):
        # "espanyol barcelona" resembles Espanyol and Barcelona about equally,
        # so an alias settles it. Both sources call them Espanyol.
        assert normalize("RCD Espanyol de Barcelona") == "espanyol"
        assert normalize("Espanyol") == "espanyol"

    def test_keeps_deportivo_which_is_a_real_name(self):
        # "Deportivo" is the club, not a suffix — dropping it would leave
        # "La Coruna" and break the join entirely.
        assert "deportivo" in normalize("RC Deportivo La Coruña")

    def test_drops_founding_years(self):
        assert normalize("FC Schalke 04") == "schalke"

    def test_falls_back_when_every_token_is_noise(self):
        # Without the fallback this would normalise to an empty string and
        # compare equal to every other all-noise name.
        assert normalize("FC de la") != ""


class TestSimilarity:
    def test_identical_names_score_one(self):
        assert similarity("Valencia CF", "Valencia") == 1.0

    def test_unrelated_clubs_fall_below_the_accept_floor(self):
        # Character overlap alone gives unrelated names a non-trivial score
        # ("valencia" vs "athletic" share four letters), so the contract worth
        # asserting is that they stay under the threshold the matcher uses.
        assert similarity("Valencia CF", "Athletic Club") < MIN_SIDE_SIMILARITY

    def test_subset_names_score_high(self):
        assert similarity("RCD Espanyol de Barcelona", "Espanyol") >= 0.9


class TestPairMatching:
    """The reason nothing in this module scores a team in isolation."""

    def test_away_side_breaks_the_espanyol_barcelona_tie(self):
        espanyol = score_pair("RCD Espanyol de Barcelona", "Levante UD", "Espanyol", "Levante")
        barcelona = score_pair(
            "RCD Espanyol de Barcelona", "Levante UD", "Barcelona", "Athletic Club"
        )

        assert espanyol.acceptable
        assert not barcelona.acceptable

    def test_best_match_picks_the_right_event(self):
        events = [
            ("Barcelona", "Athletic Club"),
            ("Espanyol", "Levante"),
            ("Real Madrid", "Real Sociedad"),
        ]
        match = best_match("RCD Espanyol de Barcelona", "Levante UD", events, key=lambda e: e)

        assert match is not None
        assert match[0] == ("Espanyol", "Levante")

    def test_returns_none_rather_than_guessing(self):
        # No odds is a fine outcome — the fixture is still stored and shown.
        # A wrong guess would attach another club's price to the ledger.
        events = [("Bayern Munich", "Borussia Dortmund")]
        assert best_match("Valencia CF", "Real Betis", events, key=lambda e: e) is None

    def test_matches_full_registered_names(self):
        events = [("Atletico Madrid", "Real Betis")]
        match = best_match(
            "Club Atlético de Madrid",
            "Real Betis Balompié",
            events,
            key=lambda e: e,
        )
        assert match is not None

    def test_reversed_fixture_is_not_a_match(self):
        # Home and away are not interchangeable: the same two clubs meeting the
        # other way round is a different fixture with different odds.
        events = [("Levante", "Espanyol")]
        assert (
            best_match("RCD Espanyol de Barcelona", "Levante UD", events, key=lambda e: e) is None
        )
