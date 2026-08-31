# Axon Peripheral SDK Notes #

## Initial Observations ##
* `axon::MyelinFrame::MyelinFrame(std::span<uint8_t>)` — frame constructed from a byte span
* `axon::recv_frame(zmq::socket_t&)` — the transport is ZeroMQ (zmq)
* `axon::frames_from_points(int, std::vector<short>&)`
* `axon::MyelinFrame::serialize() / serialize(std::span<uint8_t>)`
* `axon::compute_crc32(uint32_t*, uint32_t)`
* `axon::send_axon_packet(zmq::socket_t&, uint32_t, uint32_t, std::vector<uint32_t>, bool)`
* `axon::subscribe_to_axon_messages(zmq::socket_t&, uint32_t, std::optional<uint32_t>)`
* `axon::rx_msg_to_string(uint8_t*, uint32_t)`
* `axon::to_channel_data(std::vector<MyelinFrame>&, std::vector<synapse::Channel>&)`
* `axon::wrap_axon_packet(uint16_t, uint16_t, uint32_t*, uint16_t, uint32_t*)`
* `axon::unsubscribe_to_axon_messages(zmq::socket_t&, uint32_t, std::optional<uint32_t>)`
* `axon::RecordPeripheral::effective_sample_rate_hz(double, std::vector<synapse::Channel>&)`
* `axon::RecordPeripheral::clear_impedance_waveforms()`
* `axon::RecordPeripheral::set_fpga_clk_freq_hz(uint32_t)`
* `scifi::plugin::RecordPlugin::from_peripheral_descriptor(scifi::plugin::PeripheralDescriptor&)`

## Key Namespaces ##
The two namespaces that matter:  

**1. `axon::` — the transport/protocol layer.**  

The exported, demangled symbols give the whole API:
| Symbol    |	Signature (demangled)  |
| :-------: | :----------------------: |
| `axon::wrap_axon_packet` | `(u16 hdr, u16 opcode, const u32* payload, u16 len, u32* out)` |
| `axon::send_axon_packet` | `(zmq::socket_t&, u32, u32, std::vector<u32>, bool)` |
| `axon::recv_frame` | `(zmq::socket_t&)` |
| `axon::subscribe_to_axon_messages` |	`(zmq::socket_t&, u32 header, std::optional<u32>)` |
| `axon::unsubscribe_to_axon_messages` | `(zmq::socket_t&, u32 header, std::optional<u32>)` |
| `axon::compute_crc32` |	`(const u32*, u32)` |
| `axon::MyelinFrame` | `(ctor/serialize)`	from/std::span<u8> |
| `axon::frames_from_points` |	`(int channels, const std::vector<short>&)` |
| `axon::to_channel_data` |	`(const std::vector<MyelinFrame>&, const std::vector<synapse::Channel>&)` |
| `axon::RecordPeripheral::set_fpga_clk_freq_hz` |	`(u32)` → stores at this+0x58 |
| `axon::RecordPeripheral::effective_sample_rate_hz` |	`(double, const std::vector<synapse::Channel>&)` |

Decompilation confirms the wire format and transport:

Transport is ZeroMQ PUB/SUB. send_axon_packet uses `zmq_msg_init_size`/`zmq_msg_send`; `subscribe_to_axon_messages` calls `zmq_setsockopt`(sock, 6 /*ZMQ_SUBSCRIBE*/, &header, 4) (8 bytes when the optional id is present) — messages are filtered by the packet header as the SUB topic prefix.  

Packet layout (`wrap_axon_packet`): `word0` = magic `0xC0FFEE00`; then a 16-bit header/id; then a packed word (opcode << 16) | payload_len; then the payload at byte offset 12; then a CRC32 trailer.  

Total length = payload_len + 0x1C. Max payload = 0xFE4 (4068) bytes.  

Data path: frames_from_points packs `synapse::BroadbandFrames` into `MyelinFrames`; `to_channel_data` demultiplexes received frames back into per-channel-id `synapse::Channel` data.  

**2. `scifi::plugin::` — the peripheral/plugin layer.**  

Key export:  
* `scifi::plugin::RecordPlugin::from_peripheral_descriptor(const PeripheralDescriptor&)`: It turns a small descriptor `({name, vendor, type})` into a `synapse::Peripheral` protobuf: sets name `(+0x10)`, address/vendor `(+0x18)`, type `(+0x28)`, and a has-bit `(+0x2c = 1)`.  
* `synapse::Peripheral_Type_IsValid` returns value < 6, so the `Peripheral.Type` enum has exactly 6 members (0–5).
