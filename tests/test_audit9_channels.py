"""AUDIT-9: an unreadable channels.json is kept aside, not silently overwritten by the next save."""
from redzone.channels import ChannelMap


def test_a_corrupt_channels_file_is_kept_not_overwritten(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text('{"codes": {"espn": "AAAAAAAAAAA"}, "overrides": ')   # cut off mid-write
    cm = ChannelMap(path)
    assert cm.codes == {}
    cm.set_override("g1", "youtubetv:espn")   # any save used to replace the only copy
    assert (tmp_path / "channels.json.corrupt").read_text().startswith('{"codes": {"espn"')


def test_a_channels_file_that_is_not_an_object_does_not_crash(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text("[1, 2]")
    assert ChannelMap(path).codes == {}
