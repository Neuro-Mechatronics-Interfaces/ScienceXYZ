import org.sciencexyz.exo.ExoClient;
import java.util.concurrent.atomic.AtomicReference;
// No network calls: validates loading, JNI ABI, errors, ownership and cleanup.
public class JniSmoke {
    public static void main(String[] args) throws Exception {
        ExoClient c = new ExoClient("127.0.0.1", 647, 20);
        if (!"null".equals(c.stateJson())) throw new AssertionError("initial state");
        try { c.query("forbidden"); throw new AssertionError("validation"); }
        catch (ExoClient.NativeException e) {
            if (e.status != 1 || !e.getMessage().contains("allowlist")) throw e;
        }
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread t = new Thread(() -> {
            try { c.stateJson(); failure.set(new AssertionError("owner guard")); }
            catch (IllegalStateException expected) { }
            catch (Throwable unexpected) { failure.set(unexpected); }
        });
        t.start(); t.join();
        if (failure.get() != null) throw new AssertionError(failure.get());
        c.close(); c.close();
        try { c.stateJson(); throw new AssertionError("closed handle"); }
        catch (IllegalStateException expected) { }
        System.out.println("JNI smoke passed; no device commands");
    }
}
