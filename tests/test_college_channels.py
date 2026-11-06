"""College channel routing: conference networks on YouTube TV, ACCNX/ESPN+ last."""
from redzone.channels import ChannelMap
from redzone.models import GameState, League, Team


# --- channels: college networks on YouTube TV, ACCNX/ESPN+ last ------------------------------

def _ch(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.codes = {"accn": "A1", "espnu": "U1", "cbssn": "C1", "secn": "S1", "btn": "B1", "espn": "E1", "espn2": "E2"}
    return cm


def _g(gid, *nets, lg=League.CBB):
    return GameState(lg, gid, Team("UNC"), Team("WOF"), broadcasts=list(nets))


def test_college_networks_route_to_youtube_tv(tmp_path):
    cm = _ch(tmp_path)
    for name, key in (("ACC Network", "accn"), ("ESPNU", "espnu"), ("CBSSN", "cbssn"),
                      ("SEC Network", "secn"), ("BTN", "btn"), ("ESPN2", "espn2")):
        w = cm.where(_g("g", name))
        assert w.target == f"youtubetv:{key}" and w.tunable, name
    assert "ACC Network" in cm.where(_g("g", "ACC Network")).label


def test_accnx_and_espn_plus_go_to_espn_app_last(tmp_path):
    cm = _ch(tmp_path)
    g = _g("g2", "ACCNX")
    assert cm.where(g).target == "youtubetv:espnplus" and not cm.where(g).tunable
    cm.espn_ids["g2"] = "abc123"
    assert cm.where(g).target == "espn:abc123"
    assert cm.where(_g("g3", "ESPN+", "ACC Network")).target == "youtubetv:accn"   # the real channel wins


def test_college_football_on_broadcast_goes_to_local_channel(tmp_path):
    cm = _ch(tmp_path)
    assert cm.where(_g("g4", "ABC", lg=League.CFB)).target == "youtubetv:abc"     # never Sunday Ticket
    assert cm.where(_g("g5", "CW", lg=League.CFB)).target == "youtubetv:cw"


def test_espn_app_links_are_not_tunable_unless_turned_on(tmp_path):
    """Nov 2-5: 0 of 15 ESPN app links opened on the Fire TV ESPN app ("unable to resolve Intent"),
    and each cost a failed 3-21 s tune (Nov 5 10:12 PM: the night's first pick). tuners.espn_links."""
    cm = _ch(tmp_path)
    g = _g("g2", "ACCNX")
    cm.espn_ids["g2"] = "abc123"
    assert cm.where(g).target == "espn:abc123" and not cm.where(g).tunable
    cm.espn_links = True
    assert cm.where(g).tunable
