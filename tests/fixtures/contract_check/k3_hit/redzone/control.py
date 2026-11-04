def flip(stick):
    """Send HOME with input keyevent (a docstring is not I/O)."""
    return stick.shell("input keyevent 3")
