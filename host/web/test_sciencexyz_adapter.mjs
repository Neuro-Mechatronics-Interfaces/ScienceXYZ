import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installScienceXYZ } from './ScienceXYZTaskAdapter.js';

test('recording state changes only after successful acknowledgment', async () => {
    let resolve;
    const states = [];
    const client = {
        sendApiRequest: () => new Promise(r => { resolve = r; }),
        _setRecordingState: state => states.push(state),
    };
    const api = installScienceXYZ(client);
    const starting = api.startRecording();
    assert.deepEqual(states, []);
    resolve({api_response: {success: true, result: {recording: true}}});
    await starting;
    assert.deepEqual(states, [true]);
    const stopping = api.stopRecording();
    resolve({api_response: {success: false, error: 'write failed'}});
    await assert.rejects(stopping, /write failed/);
    assert.deepEqual(states, [true]);
});

test('uncertain task request times out without replay', async () => {
    let calls = 0;
    const api = installScienceXYZ({sendApiRequest() { calls++; return new Promise(() => {}); }}, {timeoutMs: 5});
    await assert.rejects(api.startTask(), /Uncertain command outcome/);
    assert.equal(calls, 1);
});

test('legacy recording buttons use explicit bridge requests', async () => {
    const commands = [];
    const client = {
        async sendApiRequest(body) {
            commands.push(body.sciencexyz_request);
            return {api_response: {success: true, result: {}}};
        },
        _setRecordingState() {},
    };
    installScienceXYZ(client);
    await client.sendStartRecordingRequest();
    await client.sendStopRecordingRequest();
    assert.deepEqual(commands.map(c => c.command), ['start_recording', 'stop_recording']);
    assert.equal(typeof commands[0].metadata.browser_time_origin_ms, 'number');
});
