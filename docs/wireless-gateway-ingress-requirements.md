# LAN wireless gateway requirements (copy/paste brief)

Use this brief when implementing or reviewing a phone/tablet/laptop gateway
that forwards wireless L2CAP batches to a ScienceXYZ ZMQ reader on the same
Wi-Fi network.

## Required topology

The mobile or desktop gateway is the ZeroMQ `PUB` sender. The ScienceXYZ App
or host adapter is the `SUB` reader and calls `connect()` to the gateway. Use
one logical source and one exact topic per configured reader. The wire message
is exactly two ZeroMQ frames: UTF-8 topic `wireless/v1/<source_id>`, followed by
serialized protobuf `sciencexyz.wireless.v1.WirelessBatch` bytes.

For a real LAN, bind the gateway to `tcp://0.0.0.0:<port>` or its Wi-Fi
interface and configure the reader with `tcp://<gateway-LAN-IP>:<port>`.
Never use `localhost` across devices. Give each source a stable `source_id`
and a unique TCP port or endpoint. Start with IPv4 addresses; add IPv6 only
after routing and firewall tests pass.

The TCP connection is initiated by the `SUB` reader to the gateway's `PUB`
socket. The gateway therefore needs an inbound firewall rule; the reader
needs an outbound route. If the reader is an App running on SciFi-2, verify
reachability from the SciFi runtime itself—reachability from the operator's
laptop is not sufficient.

The phone/tablet/laptop and SciFi/host must be on the same routed subnet.
Disable Wi-Fi client/AP isolation, guest-network segregation, and VPN routes
that prevent peer-to-peer traffic. The gateway's OS firewall must allow
inbound TCP on the selected port from the ScienceXYZ host/SciFi subnet.

## Platform prerequisites

* Android: request `BLUETOOTH_SCAN`/`BLUETOOTH_CONNECT` as required by the
  target Android API, obtain the user-approved nearby-device permission, and
  request `INTERNET` for the LAN socket. Handle Doze/background limits and
  keep the acquisition service alive while publishing. Test on the real Wi-Fi
  rather than assuming emulator networking matches a handset.
* iOS/iPadOS: request the appropriate Bluetooth permission and include
  `NSLocalNetworkUsageDescription` when using the local network. If the app
  must acquire while backgrounded, configure and test the permitted Bluetooth
  background mode; do not assume an ordinary background app can maintain an
  indefinite stream. A raw TCP connection does not require multicast
  discovery; configure the reader by IP first.
* Windows: mark the Wi-Fi network Private where appropriate and allow the
  gateway executable through Windows Defender Firewall for the selected TCP
  port. Do not open the port on Public networks unless an operator explicitly
  accepts that risk.
* macOS: allow the app through the application firewall and approve Local
  Network access if the OS prompts. If discovery is added later, declare and
  test the required local-network/mDNS permissions separately from the raw TCP
  path.

## Gateway behavior

The gateway must:

* drain the L2CAP input without blocking the LAN publisher;
* preserve `source_id`, `boot_session_id`, `batch_sequence`,
  `first_sample_sequence`, native tick/acquisition anchor, rational sample
  rate, channel shape/layout, and raw payload;
* set gateway receive/send monotonic timestamps and a gateway clock id;
* use a bounded buffer and report sender-known drops in `sender_gap`;
* keep sequence numbers monotonic within a session and start a new session on
  restart/reset;
* buffer/replay explicitly if recording-grade recovery is needed; plain
  PUB/SUB provides no delivery acknowledgement or replay; and
* expose clear startup, disconnect, reconnect, queue-overflow, and malformed
  input diagnostics.

Do not replace a source timestamp with a gateway, App, or host receipt time.
Do not interpolate, resample, or silently drop missing data in the gateway.
Do not claim synchronization accuracy until sequence continuity, LAN
reachability, PUB/SUB startup behavior, reconnect behavior, and clock-model
uncertainty have been measured.

## Required bring-up checks

1. From the reader machine, verify the gateway IP is reachable and the TCP
   port is permitted by both Wi-Fi routing and the gateway firewall.
2. Start the reader before the publisher and confirm the publisher waits for
   the subscription handshake or reports the initial slow-joiner interval.
3. Confirm exact topic filtering and validate one serialized batch against
   `protocol/wireless/v1/wireless_batch.proto`.
4. Stop/restart the gateway and Wi-Fi connection. Confirm a new session or
   explicit replay, reconnect backoff, and observable sequence-gap diagnostics.
5. Fill the L2CAP and publisher buffers deliberately. Confirm bounded behavior
   and an explicit drop count rather than silent loss.
6. Run four independent readers/sources through the round-robin mux and verify
   source identity, sequence, shape, rate, and clock metadata remain separated.

v1 has no authentication or encryption. Keep it on a trusted isolated lab LAN,
or place the stream behind an authenticated VPN/tunnel; never expose a raw
ZeroMQ PUB port to the public Internet. Treat payloads as untrusted input and
enforce configured size, format, source, topic, rate, and channel limits.
