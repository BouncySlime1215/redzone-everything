import time

from redzone import config


class Shadow:
    FLIP_S = config.FLIP_S


def wait():
    time.sleep(config.FLIP_S)
