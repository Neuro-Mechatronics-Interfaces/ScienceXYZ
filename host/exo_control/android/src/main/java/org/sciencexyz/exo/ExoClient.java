package org.sciencexyz.exo;

import java.nio.charset.StandardCharsets;

/** Synchronous native client. Construct, use, poll and close on ONE background
 * executor thread. close() reports an unconfirmed OFF, then frees the handle.
 * Device config gates remain authoritative. This class never starts the App. */
public final class ExoClient implements AutoCloseable {
    static { System.loadLibrary("exo_control"); System.loadLibrary("exo_jni"); }
    public static final int OFF=1, EXTERNAL=2, DECODE=3, CONNECTED=4;
    public static final int THUMB=1, INDEX=2, MIDDLE=3, RING=4, PINKY=5, WRIST=6;
    private long handle;
    private final Thread owner = Thread.currentThread();

    public ExoClient(String host, int port, int timeoutMs) {
        handle = create(utf8(host), port, timeoutMs);
    }
    private static byte[] utf8(String value) {
        if (value == null || value.indexOf('\0') >= 0) throw new IllegalArgumentException("null/NUL string");
        return value.getBytes(StandardCharsets.UTF_8);
    }
    private void check() {
        if (Thread.currentThread() != owner) throw new IllegalStateException("use the owning executor thread");
        if (handle == 0) throw new IllegalStateException("client closed");
    }
    public void connect() { check(); invoke(handle, 0, 0, null, null, null); }
    public void disconnect() { check(); invoke(handle, 1, 0, null, null, null); }
    /** Returns protobuf JSON CommandResult; SUCCEEDED acknowledges the App,
     * not measured physical movement. */
    public String setMode(int mode) { check(); return invoke(handle, 2, mode, null, null, null); }
    public String setPose(int[] joints, int[] values) {
        check();
        if (joints == null || values == null || joints.length != values.length || joints.length < 1 || joints.length > 6)
            throw new IllegalArgumentException("matching arrays with 1..6 joints required");
        return invoke(handle, 3, 0, null, joints.clone(), values.clone());
    }
    public String query(String query) { check(); return invoke(handle, 4, 0, utf8(query), null, null); }
    public String raw(String command) { check(); return invoke(handle, 5, 0, utf8(command), null, null); }
    public String requestState() { check(); return invoke(handle, 6, 0, null, null, null); }
    /** Poll periodically while idle. Returns latest full StateSnapshot JSON or
     * "null" if none received; exo.lastReply is asynchronous, not request-correlated. */
    public String pollState(int timeoutMs) { check(); return invoke(handle, 7, timeoutMs, null, null, null); }
    public String stateJson() { check(); return invoke(handle, 8, 0, null, null, null); }
    @Override public void close() {
        if (handle == 0) return;
        check();
        long old = handle;
        handle = 0;
        destroy(old);
    }
    public static final class NativeException extends RuntimeException {
        public final int status;
        public final String resultJson;
        NativeException(int status, String message, String resultJson) {
            super(message); this.status = status; this.resultJson = resultJson;
        }
    }
    private static native long create(byte[] host, int port, int timeoutMs);
    private static native String invoke(long handle, int op, int argument, byte[] text, int[] joints, int[] values);
    private static native void destroy(long handle);
}
