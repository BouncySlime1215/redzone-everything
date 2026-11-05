import sys, time, json, subprocess
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from t1_retune import read, sh, tune
H = "100.110.165.181:5555"
def E(): 
    r = read(H); return round(r["E"], 2), r["st"]
print("start", E())
tune(H, "espn"); time.sleep(3); e0, _ = E(); print("live espn E0", e0)
for pause in (10, 30):
    sh(H, "input keyevent KEYCODE_MEDIA_PAUSE"); t = time.time(); time.sleep(pause)
    print("paused", E())
    sh(H, "input keyevent KEYCODE_MEDIA_PLAY"); time.sleep(4)
    e, st = E(); print(f"after {pause}s pause: state={st} behind_meter={e - e0:+.2f}s (true pause {time.time() - t - 4:.1f}s incl. resume)")
r = tune(H, "espn"); time.sleep(1); e, st = E(); print(f"relink: play_s={r['play_s']} behind_meter={e - e0 - (time.time() - 0) * 0:+.2f}s raw; L={time.time() - e:.2f}")
