"""The end-to-end night: the whole system through a replayed night, against fake sticks and a fake TV.

    python -m redzone.lab.night                       # the full night (~8 h of games), JSON report
    python -m redzone.lab.night --hours 2 --start 20:00
    python -m redzone.lab.night --faults mypkg.faults:night   # plug in more faults (see faults.py)

Run it as its own process: the virtual clock (vclock.py) must replace time.time before any redzone
module is imported. tests/test_e2e_night.py does exactly that and asserts on the report.
Nothing here imports redzone at package import time.
"""
