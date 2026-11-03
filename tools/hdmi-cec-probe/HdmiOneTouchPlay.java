import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Experimental shell-UID helper, not part of the RedZone service.
 * Uses the device's named platform interfaces and its Binder callback wrapper.
 * No raw transactions, permission grants, accessibility overrides, or API exemptions.
 * Source only: not compiled or validated on Fire OS. See README.md before use.
 */
public final class HdmiOneTouchPlay {
    private static final long TIMEOUT_SECONDS = 25;

    public static void main(String[] args) {
        if (args.length != 1 || !(args[0].equals("--probe") || args[0].equals("--activate"))) {
            System.err.println("Usage: HdmiOneTouchPlay --probe|--activate");
            System.exit(2);
        }
        try {
            int uid = ((Integer) Class.forName("android.os.Process")
                    .getMethod("myUid").invoke(null)).intValue();
            if (uid != 2000) {
                throw new SecurityException("Expected existing ADB shell UID 2000; refusing other identities");
            }
            Class<?> binderType = Class.forName("android.os.IBinder");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class).invoke(null, "hdmi_control");
            if (binder == null) {
                throw new IllegalStateException("No hdmi_control service");
            }
            Class<?> serviceType = Class.forName("android.hardware.hdmi.IHdmiControlService");
            Object service = Class.forName("android.hardware.hdmi.IHdmiControlService$Stub")
                    .getMethod("asInterface", binderType).invoke(null, binder);
            // This named read is permission-enforced by AOSP HdmiControlService.
            int[] types = (int[]) serviceType.getMethod("getSupportedTypes").invoke(service);
            Class<?> deviceInfoType = Class.forName("android.hardware.hdmi.HdmiDeviceInfo");
            int playbackType = deviceInfoType.getField("DEVICE_PLAYBACK").getInt(null);
            boolean playbackSupported = false;
            if (types != null) {
                for (int type : types) {
                    playbackSupported |= type == playbackType;
                }
            }
            if (!playbackSupported) {
                throw new IllegalStateException("No playback CEC device: " + Arrays.toString(types));
            }
            Class<?> managerType = Class.forName("android.hardware.hdmi.HdmiControlManager");
            Class<?> playbackClass = Class.forName("android.hardware.hdmi.HdmiPlaybackClient");
            Class<?> callbackType = Class.forName(
                    "android.hardware.hdmi.HdmiPlaybackClient$OneTouchPlayCallback");
            Method oneTouchPlay = playbackClass.getMethod("oneTouchPlay", callbackType);
            // Resolve without constructing a manager or registering listeners in probe mode.
            managerType.getConstructor(serviceType);
            managerType.getMethod("getPlaybackClient");
            final int success = managerType.getField("RESULT_SUCCESS").getInt(null);
            System.out.println("Named interface available; CEC playback type present; shell UID=" + uid);
            if (args[0].equals("--probe")) {
                System.out.println("Probe only: no activation requested");
                System.exit(0);
            }

            Object manager = managerType.getConstructor(serviceType).newInstance(service);
            Object playback = managerType.getMethod("getPlaybackClient").invoke(manager);
            if (playback == null) {
                throw new IllegalStateException("getPlaybackClient returned null");
            }
            CountDownLatch completed = new CountDownLatch(1);
            AtomicInteger result = new AtomicInteger(Integer.MIN_VALUE);
            Object callback = Proxy.newProxyInstance(callbackType.getClassLoader(),
                    new Class<?>[] {callbackType}, (proxy, method, values) -> {
                        if (method.getName().equals("onComplete")) {
                            result.set(((Integer) values[0]).intValue());
                            completed.countDown();
                            return null;
                        }
                        if (method.getName().equals("toString")) return "HdmiOneTouchPlayCallback";
                        if (method.getName().equals("hashCode")) return System.identityHashCode(proxy);
                        if (method.getName().equals("equals")) return proxy == values[0];
                        throw new UnsupportedOperationException(method.getName());
                    });
            System.out.println("Requesting oneTouchPlay once; foreground app is not selected by this helper");
            oneTouchPlay.invoke(playback, callback);
            if (!completed.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                System.err.println("Callback timed out; delivery unknown. Do not automatically retry");
                System.exit(4);
            }
            System.out.println("CEC callback result=" + result.get()
                    + "; success=" + (result.get() == success)
                    + "; visible input and continued playback remain unverified");
            System.exit(result.get() == success ? 0 : 5);
        } catch (Throwable error) {
            while (error instanceof InvocationTargetException && error.getCause() != null) {
                error = error.getCause();
            }
            System.err.println("Stopped: " + error.getClass().getName() + ": " + error.getMessage());
            System.err.println("No fallback, retry, permission change, or hidden-API workaround attempted");
            System.exit(3);
        }
    }
}
