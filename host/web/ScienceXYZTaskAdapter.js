// Install beside the existing Reactions TaskSocketClient; no helper imports.
// The bridge adds explicit commands without pretending to implement CTRL-R.
export function installScienceXYZ(client, { timeoutMs = 30000 } = {}) {
    const originalRequest = client.sendApiRequest.bind(client);
    client.sendApiRequest = async (body) => {
        const response = await originalRequest(body);
        if (response.api_response?.success === false) throw new Error(response.api_response.error);
        return response;
    };
    async function request(command, metadata) {
        let timer;
        try {
            const reply = await Promise.race([
                client.sendApiRequest({ sciencexyz_request: { command, metadata } }),
                new Promise((_, reject) => {
                    timer = setTimeout(() => reject(new Error("Uncertain command outcome; do not retry. Inspect recording/task state.")), timeoutMs);
                }),
            ]);
            if (!reply.api_response?.success) throw new Error(reply.api_response?.error || "Bridge request failed");
            return reply.api_response.result;
        } finally { clearTimeout(timer); }
    }
    const api = {
        profile: () => request("get_profile"),
        startRecording: async (metadata = {}) => {
            const result = await request("start_recording", {
                ...metadata,
                browser_time_origin_ms: performance.timeOrigin,
                browser_performance_ms: performance.now(),
                browser_unix_ms: Date.now(),
            });
            client._setRecordingState(true);
            return result;
        },
        stopRecording: async () => {
            const result = await request("stop_recording");
            client._setRecordingState(false);
            return result;
        },
        startTask: () => request("start_task"),
        advanceTask: () => request("propose_task_event"),
        resetTask: () => request("reset_task"),
        abortTask: () => request("abort_task"),
        // Record UI presentation separately; event payloads never become labels.
        presentation: async (event, payload = {}) => {
            await client.startStream("sciencexyz_presentation");
            return client.sendStreamSample("sciencexyz_presentation", [{
                browser_time_origin_ms: performance.timeOrigin,
                browser_performance_ms: performance.now(),
                data: { event, payload },
            }]);
        },
    };
    client.sendStartRecordingRequest = () => api.startRecording({
        task_name: client._taskName, task_parameters: client._taskParameters,
        subject: client._subject, side: client._side,
    });
    client.sendStopRecordingRequest = () => api.stopRecording();
    return api;
}
