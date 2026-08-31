import tempfile
import unittest
from pathlib import Path

import pandas as pd

from advisor.sosfanta_goalkeeper_updates import (
    GOALKEEPER_URL,
    apply_update,
    extract_goalkeeper_hierarchies,
    fetch_snapshot,
)
from advisor.sosfanta_updates import SosFantaError


TEAMS = ["Atalanta", "Bologna", "Cagliari", "Como", "Fiorentina", "Frosinone", "Genoa", "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Monza", "Napoli", "Parma", "Roma", "Sassuolo", "Torino", "Udinese", "Venezia"]


def article():
    blocks = []
    for index, team in enumerate(TEAMS):
        first, second, third = f"First{index}", f"Second{index}", f"Third{index}"
        if team == "Frosinone":
            first, second = "First5/Second5", "Second5/First5"
        if team == "Monza":
            second = "Arriverà dal mercato un nuovo portiere"
        tag = "b" if team == "Monza" else "strong"
        blocks.append(f'<p>🧤 <{tag}>{team.upper()}</{tag}></p><p><em>Primo</em>: <strong>{first}</strong></p><div class="ad"></div><p><em>Secondo</em>: {second}</p><p><em>Terzo</em>: {third}</p><p><em>Note</em>: Note {team}</p>')
    return '<h1>Primi, secondi e terzi portieri al fantacalcio per la Serie A 2026/27</h1><div id="article-content"><p>Intro</p>' + ''.join(blocks) + '</div>'


class GoalkeeperUpdateTests(unittest.TestCase):
    def test_parser_handles_ads_b_headings_and_order(self):
        teams = extract_goalkeeper_hierarchies(article(), "2026/27")
        self.assertEqual(len(teams), 20)
        self.assertEqual(teams[5]["primo"], "First5/Second5")
        self.assertEqual(teams[12]["secondo"], "Arriverà dal mercato un nuovo portiere")

    def test_parser_rejects_incomplete_article(self):
        with self.assertRaises(SosFantaError):
            extract_goalkeeper_hierarchies(article().replace("<em>Note</em>: Note Venezia", "<em>Other</em>: Note Venezia"), "2026/27")

    def test_snapshot_is_semantic_and_uses_fixed_url(self):
        snapshot = fetch_snapshot("2026/27", lambda _: article())
        self.assertEqual(snapshot["urls"], [GOALKEEPER_URL])
        self.assertEqual(snapshot["content_hash"], fetch_snapshot("2026/27", lambda _: article())["content_hash"])

    def test_apply_updates_and_adds_resolved_listone_players(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            starters = root / "titolari.csv"
            starters.write_text("squadra,nome,id_fantacalcio,status,note\nAtalanta,First0,1,RISERVA,old\n", encoding="utf-8")
            listone = root / "listone.xlsx"
            rows = []
            for index, team in enumerate(TEAMS):
                for rank, name in enumerate((f"First{index}", f"Second{index}", f"Third{index}"), 1):
                    rows.append({"Id": index * 10 + rank, "R": "P", "Nome": name, "Squadra": team})
            with pd.ExcelWriter(listone) as writer:
                pd.DataFrame([{"season": "2026/27"}]).to_excel(writer, sheet_name="Tutti", index=False, startrow=0)
                pd.DataFrame(rows).to_excel(writer, sheet_name="Tutti", index=False, startrow=1)
                pd.DataFrame([{"banner": "2026/27"}]).to_excel(writer, sheet_name="Ceduti", index=False)
                pd.DataFrame(columns=["Id", "R", "RM", "Nome", "Squadra"]).to_excel(writer, sheet_name="Ceduti", index=False, startrow=1)
            snapshot = fetch_snapshot("2026/27", lambda _: article())
            snapshot_dir = root / "updates" / "p" / "2026-27" / "sosfanta-goalkeepers-v1"
            snapshot_dir.mkdir(parents=True)
            import json
            (snapshot_dir / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
            result = apply_update(root / "updates", "p", "2026/27", starters, listone, snapshot["content_hash"], lambda: None)
            self.assertEqual(result["updated_rows"], 59)
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual(result["starters_path"], str(starters.resolve()))
            self.assertEqual(len(result["starters_hash"]), 64)
            updated = pd.read_csv(starters, dtype=str)
            self.assertEqual(len(updated), 59)
            self.assertEqual(updated.loc[updated.nome == "First0", "status"].iloc[0], "TITOLARE")
            self.assertEqual(updated.loc[updated.nome == "First0", "gerarchia_portiere"].iloc[0], "PRIMO")
            self.assertEqual(set(updated.loc[updated.nome.isin(["First5", "Second5"]), "status"]), {"BALLOTTAGGIO"})
            self.assertEqual(set(updated.loc[updated.nome.isin(["First5", "Second5"]), "gerarchia_portiere"]), {"PRIMO/SECONDO"})


if __name__ == "__main__":
    unittest.main()
