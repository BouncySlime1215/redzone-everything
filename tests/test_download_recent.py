"""Exact-date public archive fetches preserve both date boundaries and failed evidence."""
import json

import httpx

from scripts.download_recent import download_day, event_date, parse_date


def test_late_sunday_game_belongs_to_eastern_date():
    assert event_date({'date': '2026-11-02T01:20Z'}) == parse_date('20261101')
    assert event_date({'date': '2026-11-02T06:20Z'}) == parse_date('20261102')


def test_http_failure_is_not_saved_as_an_empty_sports_day(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))) as client:
        result = download_day(client, 'nfl', parse_date('20261101'), tmp_path)
    assert result['status'] == 'error' and result['errors']
    assert not (tmp_path / 'scoreboard_20261101.json').exists()
    assert list(tmp_path.glob('download_manifest_20261101_*.json'))


def test_wrong_summary_identity_never_enters_archive(tmp_path):
    event = {'id': '123', 'date': '2026-11-02T01:20Z', 'status': {'type': {'state': 'post'}}}
    def response(request):
        if request.url.path.endswith('scoreboard'):
            return httpx.Response(200, json={'events': [event]})
        return httpx.Response(200, json={'header': {'id': '999'}})
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        result = download_day(client, 'nfl', parse_date('20261101'), tmp_path)
    assert result['status'] == 'partial' and result['errors']
    assert not (tmp_path / 'summary_123.json').exists()
    assert json.loads((tmp_path / 'scoreboard_20261101.json').read_text())['events'][0]['id'] == '123'
