// [SYNC diamond A2] Read every media session on a Fire TV stick and seek one to an exact position.
//
// Runs as the adb shell user through app_process (no APK, nothing installed; see build.sh / README.md):
//   adb push rzseek.dex /data/local/tmp/
//   adb shell CLASSPATH=/data/local/tmp/rzseek.dex app_process / RzSeek list
//   adb shell CLASSPATH=/data/local/tmp/rzseek.dex app_process / RzSeek seek <package> <position_ms>
//   adb shell CLASSPATH=/data/local/tmp/rzseek.dex app_process / RzSeek seekrel <package> <delta_ms>
//
// Both YouTube TV (actions=383) and Prime (1049455) advertise SEEK_TO (bit 256), overseer Nov 5. `list` prints one
// JSON line per session with the position NOW extrapolated on the stick's own clock (no adb round trip in it).
// Everything goes through reflection, so it compiles with a plain JDK (no android.jar) and tolerates hidden-API
// differences between Fire OS 7 (Android 9) and Fire OS 8 (Android 11). The shell user holds
// MEDIA_CONTENT_CONTROL, which getActiveSessions(null) needs. Seeking is the only thing it can change.
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;

public class RzSeek {
    static Object call(Object o, String name, Object... args) throws Exception {
        for (Method m : o.getClass().getMethods()) {
            if (m.getName().equals(name) && m.getParameterTypes().length == args.length) {
                return m.invoke(o, args);
            }
        }
        throw new NoSuchMethodException(o.getClass().getName() + "." + name);
    }

    static Object context() throws Exception {
        Class<?> looper = Class.forName("android.os.Looper");
        try {
            looper.getMethod("prepareMainLooper").invoke(null);
        } catch (Exception ignored) {
            // already prepared
        }
        Class<?> atc = Class.forName("android.app.ActivityThread");
        Constructor<?> c = atc.getDeclaredConstructor();
        c.setAccessible(true);
        Object at = c.newInstance();
        Field f = atc.getDeclaredField("sCurrentActivityThread");
        f.setAccessible(true);
        f.set(null, at);
        return atc.getMethod("getSystemContext").invoke(at);
    }

    static String esc(Object s) {
        return s == null ? "null" : "\"" + s.toString().replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.out.println("{\"error\":\"usage: list | seek <pkg> <ms> | seekrel <pkg> <delta_ms>\"}");
            return;
        }
        Object ctx = context();
        Object msm = call(ctx, "getSystemService", "media_session");
        Method gas = msm.getClass().getMethod("getActiveSessions", Class.forName("android.content.ComponentName"));
        List<?> ctrls = (List<?>) gas.invoke(msm, (Object) null);
        Method elapsed = Class.forName("android.os.SystemClock").getMethod("elapsedRealtime");
        long wall0 = System.currentTimeMillis();
        long now = (Long) elapsed.invoke(null);
        long wall1 = System.currentTimeMillis();
        String cmd = args[0];
        boolean done = false;
        for (Object mc : ctrls) {
            String pkg = (String) call(mc, "getPackageName");
            Object st = call(mc, "getPlaybackState");
            int state = st == null ? -1 : (Integer) call(st, "getState");
            long pos = st == null ? -1 : (Long) call(st, "getPosition");
            long upd = st == null ? -1 : (Long) call(st, "getLastPositionUpdateTime");
            float speed = st == null ? 0f : (Float) call(st, "getPlaybackSpeed");
            long actions = st == null ? 0 : (Long) call(st, "getActions");
            long posNow = pos;
            if (state == 3 && upd > 0) {   // PLAYING: run the position on from its update on the stick's clock
                posNow = pos + (long) (speed * (now - upd));
            }
            if (cmd.equals("list")) {
                System.out.println("{\"pkg\":" + esc(pkg) + ",\"state\":" + state + ",\"position\":" + pos
                        + ",\"updated\":" + upd + ",\"speed\":" + speed + ",\"actions\":" + actions
                        + ",\"elapsed\":" + now + ",\"pos_now\":" + posNow
                        + ",\"wall_ms\":" + (wall0 + wall1) / 2 + "}");
            } else if ((cmd.equals("seek") || cmd.equals("seekrel")) && args.length >= 3 && pkg.equals(args[1])) {
                if ((actions & 256L) == 0) {
                    System.out.println("{\"pkg\":" + esc(pkg) + ",\"error\":\"no SEEK_TO in actions\",\"actions\":"
                            + actions + "}");
                    return;
                }
                long target = cmd.equals("seek") ? Long.parseLong(args[2]) : posNow + Long.parseLong(args[2]);
                Object tc = call(mc, "getTransportControls");
                tc.getClass().getMethod("seekTo", long.class).invoke(tc, target);
                System.out.println("{\"pkg\":" + esc(pkg) + ",\"seek_to\":" + target + ",\"from\":" + posNow
                        + ",\"elapsed\":" + now + "}");
                done = true;
                break;
            }
        }
        if (!cmd.equals("list") && !done) {
            System.out.println("{\"error\":\"no session for that package\"}");
        }
    }
}
