import pytest

from redzone.tuners.adb import RunResult, Stick, hdmi_port_from_dump


@pytest.mark.parametrize(('text', 'expected'), [
    ('physical_address: 0x2000', 'HDMI2'),
    ('physical_address: 0x1000', 'HDMI1'),
    ('mPhysicalAddress=0x2000', 'HDMI2'),
    ('physical address: 1.0.0.0', 'HDMI1'),
    ('physical_address: 0xffff', None),
    ('physical_address: 0x0000', None),
    ('physical_address: 0x2100', None),
    ('physical_address: 0x1000\nphysical_address: 0x2000', None),
    ('ActiveSource: (4, 0x2000)', None),
])
def test_only_labeled_unambiguous_direct_topology_identifies_port(text, expected):
    assert hdmi_port_from_dump(text) == expected


async def test_read_command_accepts_fire_os_underscore_field(monkeypatch):
    stick = Stick('fake')
    async def shell(command, timeout):
        assert "physical[ _]?address" in command
        return RunResult(True, 'physical_address: 0x2000', 0.01)
    monkeypatch.setattr(stick, 'shell', shell)
    assert await stick.hdmi_port() == 'HDMI2'


async def test_failed_dump_never_reports_a_port_from_partial_output(monkeypatch):
    stick = Stick('fake')
    async def shell(command, timeout):
        return RunResult(False, 'physical_address: 0x2000', 0.01)
    monkeypatch.setattr(stick, 'shell', shell)
    assert await stick.hdmi_port() is None
