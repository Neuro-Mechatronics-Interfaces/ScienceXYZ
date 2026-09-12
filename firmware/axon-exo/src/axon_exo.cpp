#include "scifi-peripheral-sdk/plugin.h"
#include "scifi-peripheral-sdk/axon/io.h"
#include <spdlog/spdlog.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <cmath>

namespace axon_exo {
using Clock = std::chrono::steady_clock;
using Status = scifi::Status;
// Axon sockets belong exclusively to the polling thread. read_frames only
// drains a bounded queue; it never waits for USB or a Dynamixel transaction.
class Peripheral final : public axon::RecordPeripheral {
 public:
  Peripheral(uint32_t id, uint32_t address, zmq::context_t& ctx, const char* tx, const char* rx)
      : RecordPeripheral(id,20,16,1,1024), address_(address),ctx_(ctx),tx_(tx),rx_(rx) {}
  // safe_make uses make_shared<Peripheral>, preserving the concrete deleter.
  ~Peripheral() { stop_recording(); }
  synapse::Peripheral to_proto() const override {
    synapse::Peripheral p;
    p.set_name("NML Hand Exo"); p.set_vendor("NML"); p.set_peripheral_id(id);
    p.set_type(synapse::Peripheral::kBroadbandSource); return p;
  }
  synapse::QueryResponse self_test(const synapse::SelfTestQuery&) override {
    synapse::QueryResponse r; r.mutable_status()->set_code(synapse::StatusCode::kUnimplemented);
    r.mutable_status()->set_message("Physical self-test is operator supervised"); return r;
  }
  const std::optional<std::string> validate_channels(const std::vector<synapse::Channel>& ch) const override {
    if (ch.empty() || ch.size()>72 || ch.size()%4) return "select four channels per motor, at most 18 motors";
    for (size_t i=0;i<ch.size();++i) {
      const unsigned motor=ch[i].electrode_id()/4;
      if (ch[i].type()!=synapse::ELECTRODE || ch[i].id()!=i || motor<1 || motor>252 ||
          ch[i].electrode_id()%4!=i%4 || ch[i].reference_id()!=0 ||
          motor!=ch[i-i%4].electrode_id()/4) return "invalid Exo layout (angle, age low, age high, status)";
      if (i%4==0) for (size_t j=0;j<i;j+=4)
        if (ch[j].electrode_id()/4==motor) return "duplicate motor ID";
    }
    return std::nullopt;
  }
  const std::optional<std::string> validate_ephys_config(const synapse::BroadbandSourceConfig& c) const override {
    if (c.sample_rate_hz()<1 || c.sample_rate_hz()>20 || c.bit_width()!=16 ||
        (c.gain()!=0 && c.gain()!=1)) return "Exo supports 1..20 Hz, 16 bits, unity gain";
    if (!c.signal().has_electrode()) return "Exo requires an electrode channel container (numeric telemetry, not voltage)";
    const auto& e=c.signal().electrode();
    if (e.low_cutoff_hz()>0 || e.high_cutoff_hz()>0) return "Exo telemetry does not support analog filters";
    return validate_channels({e.channels().begin(),e.channels().end()});
  }
  Status start_recording(uint32_t rate,uint32_t bits,std::vector<synapse::Channel> ch,
                         float gain,float hp,float lp) override {
    std::lock_guard<std::mutex> lifecycle(lifecycle_);
    if (worker_.joinable()) return Status::INVALID_STATE;
    if (rate<1 || rate>20 || bits!=16 || (gain!=0 && gain!=1) || hp>0 || lp>0 || validate_channels(ch))
      return Status::INVALID_PARAMETER;
    channels=std::move(ch); rate_=rate;
    synapse::ChannelRange range; range.set_type(synapse::ELECTRODE); range.set_count(channels.size());
    for (const auto& c:channels) range.add_channel_ids(c.id());
    channel_ranges={range};
    {std::lock_guard<std::mutex> lock(mutex_); queue_.clear(); ready_=false; failed_=false;}
    stop_=false;
    try {worker_=std::thread([this]{poll();});}
    catch (const std::exception& e) {
      stop_=true; spdlog::error("Exo {} polling thread: {}",id,e.what()); return Status::FAILURE;
    }
    std::unique_lock<std::mutex> lock(mutex_);
    const bool ready=cv_.wait_for(lock,std::chrono::milliseconds(1500),[this]{return ready_||failed_;}) && ready_;
    lock.unlock();
    if (!ready) {stop_=true; worker_.join(); return Status::CONNECTION_FAILED;}
    return Status::OK;
  }
  Status stop_recording() override {
    std::lock_guard<std::mutex> lifecycle(lifecycle_); stop_=true;
    if (worker_.joinable()) worker_.join();
    std::lock_guard<std::mutex> lock(mutex_); queue_.clear(); return Status::OK;
  }
  std::vector<axon::MyelinFrame> read_frames(uint32_t n) override {
    std::vector<axon::MyelinFrame> out; std::lock_guard<std::mutex> lock(mutex_);
    while (n-- && !queue_.empty()) {out.push_back(std::move(queue_.front())); queue_.pop_front();}
    return out;
  }
  axon::ChannelData read(uint32_t n) override {return axon::to_channel_data(read_frames(n),channels);}
  float get_lsb(float,float) const override {return 1;} // raw mixed numeric fields, NOT microvolts
  Status configure_sample_rate(double desired,double& actual) override {
    actual=0;
    if (!std::isfinite(desired)||desired<1||desired>20||std::floor(desired)!=desired) return Status::INVALID_PARAMETER;
    actual=desired; return Status::OK;
  }
  Status get_impedance(uint32_t,float,float& mag,float& phase) override {mag=phase=0; return Status::UNSUPPORTED;}
 protected:
  Status configure_bit_width(uint16_t b) override {return b==16?Status::OK:Status::INVALID_PARAMETER;}
  Status configure_channels(const std::vector<synapse::Channel>& ch) override {return validate_channels(ch)?Status::INVALID_PARAMETER:Status::OK;}
 private:
  void poll() noexcept {
    try {
      zmq::socket_t tx(ctx_,zmq::socket_type::pub),rx(ctx_,zmq::socket_type::sub);
      tx.set(zmq::sockopt::linger,0); tx.set(zmq::sockopt::sndtimeo,0);
      rx.set(zmq::sockopt::linger,0); rx.set(zmq::sockopt::rcvtimeo,10); rx.set(zmq::sockopt::rcvhwm,128);
      axon::subscribe_to_axon_messages(rx,address_,0xF211); rx.connect(rx_); tx.connect(tx_);
      uint32_t token=uint32_t(Clock::now().time_since_epoch().count());
      auto next=Clock::now(); auto deadline=next; bool pending=false;
      uint64_t dropped=0,timeouts=0,rejected=0,delivered=0;
      spdlog::info("Exo {}: {} motors, {} Hz nominal polling; source uptime timestamps, alignment unknown; angle=0.1 degree, age=ms, status=0/1",id,channels.size()/4,rate_);
      while (!stop_) {
        const auto now=Clock::now();
        if (pending && now>=deadline) {pending=false; ++timeouts; spdlog::warn("Exo {} telemetry timeout {}",id,timeouts);}
        if (!pending && now>=next) {
          std::vector<uint32_t> request{1,++token,uint32_t(channels.size()/4)};
          for (size_t i=0;i<channels.size();i+=4) request.push_back(channels[i].electrode_id()/4);
          if (axon::send_axon_packet(tx,address_,0xF210,std::move(request))!=Status::OK)
            throw std::runtime_error("Axon telemetry request send failed");
          pending=true; deadline=now+std::chrono::milliseconds(250);
          next=now+std::chrono::microseconds(1000000/rate_);
        }
        zmq::message_t message;
        if (!rx.recv(message,zmq::recv_flags::none)) continue;
        const auto receipt=Clock::now();
        if (message.size()<sizeof(axon::RxMsgHeader)+20 || message.size()%4) {++rejected; continue;}
        axon::RxPacket packet(std::move(message));
        if (!pending || packet.src_addr()!=address_ || packet.type()!=0xF211 ||
            packet.payload_size()!=5+channels.size()/2 || packet[0]!=1 || packet[1]!=token || packet[4]!=channels.size()/4) {++rejected; continue;}
        const uint64_t source_ms=uint64_t(packet[2]) | uint64_t(packet[3])<<32;
        if (source_ms>UINT64_MAX/1000000) {++rejected; continue;}
        std::vector<int16_t> values; values.reserve(channels.size());
        for (size_t i=5;i<packet.payload_size();++i) {
          values.push_back(static_cast<int16_t>(packet[i]&0xFFFF));
          values.push_back(static_cast<int16_t>(packet[i]>>16));
        }
        axon::MyelinFrame frame(source_ms*1000000,rate_,packet.seq_num(),std::move(values),channel_ranges);
        frame.set_unix_timestamp_ns(std::chrono::duration_cast<std::chrono::nanoseconds>(receipt.time_since_epoch()).count());
        {std::lock_guard<std::mutex> lock(mutex_);
          if (queue_.size()==128) {++dropped; spdlog::error("Exo {} queue overflow: dropped {} frames",id,dropped);}
          else {queue_.push_back(std::move(frame)); ++delivered;}
          ready_=true;
        }
        cv_.notify_all(); pending=false;
      }
      spdlog::info("Exo {} telemetry: queued={} overflow={} timeouts={} stale/malformed={}",id,delivered,dropped,timeouts,rejected);
    } catch (const std::exception& e) {
      spdlog::error("Exo {} telemetry stopped: {}",id,e.what());
      {std::lock_guard<std::mutex> lock(mutex_); failed_=true;} cv_.notify_all();
    }
  }
  uint32_t address_,rate_=0;
  zmq::context_t& ctx_; std::string tx_,rx_;
  std::mutex lifecycle_,mutex_; std::condition_variable cv_;
  std::deque<axon::MyelinFrame> queue_; std::thread worker_; std::atomic<bool> stop_{true};
  bool ready_=false,failed_=false;
};
}
static_assert(scifi::plugin::ABI_VERSION==3);
SCIFI_REGISTER_PERIPHERAL(axon_exo::Peripheral,"axon_exo","0.2.0",0xF002u);
