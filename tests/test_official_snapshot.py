from advisor.official_snapshot import blend_current, build_snapshot


def row(player_id=7, name="Rossi", team="roma", classic="a", mantra="pc"):
    return (
        f'<tr class="player-row" data-filter-role-classic="{classic}" data-filter-role-mantra="{mantra}">'
        f'<th><a class="player-link" href="https://www.fantacalcio.it/serie-a/squadre/{team}/rossi/{player_id}">{name}</a></th>'
    )


def test_public_pages_build_a_complete_snapshot():
    quotations = row() + """
      <td data-col-key="c_qi">10</td><td data-col-key="c_qa">12</td><td data-col-key="c_fvm">99</td>
      <td data-col-key="m_qi">11</td><td data-col-key="m_qa">14</td><td data-col-key="m_fvm">101</td></tr>"""
    statistics = row() + """
      <td data-col-key="pg">2</td><td data-col-key="mv">6,5</td><td data-col-key="mfv">8</td>
      <td data-col-key="gol">1</td><td data-col-key="gs">0</td><td data-col-key="rig">1 / 2</td>
      <td data-col-key="rp">0</td><td data-col-key="ass">1</td><td data-col-key="amm">1</td><td data-col-key="esp">0</td></tr>"""

    snapshot = build_snapshot("2026/27", quotations, statistics)

    assert snapshot["players"][0]["Diff."] == 2
    assert snapshot["players"][0]["Diff.M"] == 3
    assert snapshot["statistics"][0]["R+"] == 1
    assert snapshot["statistics"][0]["R-"] == 1
    assert snapshot["observed_matchdays"] == 2


def test_current_samples_are_shrunk_toward_the_history():
    assert blend_current(6.0, 8.0, 2) == 6.4
