import tempfile
import unittest
from pathlib import Path

from advisor.sosfanta_set_piece_updates import (
    accept_latest,
    build_bundle,
    check_updates,
    extract_penalties,
    extract_set_pieces,
    penalty_url,
    semantic_diff,
    set_piece_url,
    stored_status,
)
from advisor.sosfanta_updates import SosFantaError


TEAMS = [
    "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "FIORENTINA", "FROSINONE", "GENOA",
    "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN", "MONZA", "NAPOLI", "PARMA",
    "ROMA", "SASSUOLO", "TORINO", "UDINESE", "VENEZIA",
]


def article(first_free_kicks="Alpha, Beta", first_prose="Alpha is first choice."):
    sections = []
    for index, team in enumerate(TEAMS):
        free_kicks = first_free_kicks if index == 0 else f"Free {index}, Reserve {index}"
        prose = first_prose if index == 0 else f"Stable hierarchy for {team}."
        sections.append(
            f"<p>✅ <strong>{team}</strong></p>"
            f"<p><em>Punizioni</em>: {free_kicks}</p>"
            f"<p><em>Corner</em>: Corner {index}, Wide {index}</p><p>{prose}</p>"
            f'{"<p>📌 <a>Qui un contenuto correlato</a></p>" if index == 0 else ""}'
        )
    return '<html><h1>Tiratori Serie A 2026/27</h1><div id="article-content">' + "".join(sections) + "</div><aside><p>Ignore me.</p></aside></html>"


def penalty_article(first="Penalty One", reserve="Penalty Two"):
    sections = []
    for index, team in enumerate(TEAMS):
        primary = first if index == 0 else "Penalty Player"
        alternative = reserve if index == 0 else "Reserve Player"
        sections.append(
            f"<p>🎯 <strong>{team}</strong></p>"
            f"<p><em>Primo</em>: il candidato è <strong>{primary}</strong>.</p>"
            f"<p><em>Note</em>: alle sue spalle <strong>{alternative}</strong>. "
            "<strong>Questa frase in grassetto non è un giocatore</strong>.</p>"
        )
    return '<html><h1>Tutti i rigoristi 2026/27</h1><div id="article-content">' + "".join(sections) + "</div></html>"


class SosFantaSetPieceUpdatesTests(unittest.TestCase):
    def test_builds_url_and_extracts_ordered_hierarchies(self):
        self.assertEqual(
            set_piece_url("2026/27"),
            "https://www.sosfanta.com/asta-fantacalcio/serie-a-2026-2027-tiratori-punizioni-corner-specialisti-fantacalcio-asta/",
        )
        teams = extract_set_pieces(article(), "2026/27")
        self.assertEqual(len(teams), 20)
        self.assertEqual(teams[0]["team"], "Atalanta")
        self.assertEqual(teams[0]["specialties"]["PUNIZIONI"], ["Alpha", "Beta"])
        self.assertEqual(teams[0]["specialties"]["CORNER"], ["Corner 0", "Wide 0"])
        self.assertEqual(teams[0]["paragraphs"], ["Alpha is first choice."])
        self.assertTrue(penalty_url("2026/27").endswith("rigoristi-seriea-venti-squadre-campionato/"))
        penalties = extract_penalties(penalty_article(), "2026/27")
        self.assertEqual(penalties[0]["players"], ["Penalty One", "Penalty Two"])

    def test_rejects_incomplete_or_wrong_season_articles(self):
        with self.assertRaises(SosFantaError):
            extract_set_pieces(article().replace("<h1>Tiratori Serie A 2026/27</h1>", "<h1>2025/26</h1>"), "2026/27")
        with self.assertRaises(SosFantaError):
            extract_set_pieces(article().replace("<p><em>Corner</em>: Corner 19, Wide 19</p>", ""), "2026/27")

    def test_accepts_hyphenated_season_in_live_article_titles(self):
        pieces = article().replace("Tiratori Serie A 2026/27", "Tiratori in Serie A per il fantacalcio 2026-27")
        penalties = penalty_article().replace("Tutti i rigoristi 2026/27", "Tutti i rigoristi della Serie A per il fantacalcio 2026-27")
        self.assertEqual(len(extract_set_pieces(pieces, "2026/27")), 20)
        self.assertEqual(len(extract_penalties(penalties, "2026/27")), 20)

    def test_semantic_diff_is_limited_to_changed_team_text(self):
        old = {"teams": extract_set_pieces(article(), "2026/27")}
        new = {"teams": extract_set_pieces(article(first_free_kicks="Beta, Alpha", first_prose="Beta is first choice."), "2026/27")}
        changes = semantic_diff(old, new)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["team"], "Atalanta")
        self.assertEqual(changes[0]["old_text"], ["Alpha is first choice."])
        self.assertEqual(changes[0]["new_specialties"]["PUNIZIONI"], ["Beta", "Alpha"])

    def test_check_accept_status_and_bundle_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = {"pieces": article(), "penalties": penalty_article()}
            fetcher = lambda url: version["penalties"] if "rigoristi" in url else version["pieces"]

            first = check_updates(root, "profile", "2026/27", fetcher)
            self.assertEqual(first["state"], "baseline_missing")
            accept_latest(root, "profile", "2026/27", first["content_hash"])
            self.assertEqual(stored_status(root, "profile", "2026/27")["state"], "unchanged")

            version["pieces"] = article(first_free_kicks="Beta, Alpha", first_prose="Beta moved ahead.")
            version["penalties"] = penalty_article(first="Penalty Two", reserve="Penalty One")
            changed = check_updates(root, "profile", "2026/27", fetcher)
            self.assertEqual(changed["state"], "changed")
            self.assertEqual(changed["change_count"], 1)

            source = root / "piazzati.csv"
            source.write_text(
                "squadra,nome,tipo,priorita\nAtalanta,Penalty,RIGORI,1\nAtalanta,Alpha,PUNIZIONI,1\n",
                encoding="utf-8",
            )
            bundle = build_bundle(root, "profile", "2026/27", source, changed["content_hash"])
            self.assertIn("current_piazzati_csv", bundle)
            self.assertIn("A RIGORI operation must be supported", bundle)
            self.assertIn("Beta moved ahead", bundle)
            self.assertIn("Penalty Two", bundle)
            self.assertNotEqual(
                root / "profile" / "2026-27" / "sosfanta-set-pieces-v2",
                root / "profile" / "2026-27" / "sosfanta",
            )


if __name__ == "__main__":
    unittest.main()
