#include "usb_cdc_port.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <limits>
#include <utility>

namespace scifi2_hub::exo {

bool select_cdc_endpoints(const std::vector<UsbInterface>& interfaces, int control,
                          CdcEndpoints& selected, std::string& error) {
  int data = -1;
  for (const auto& iface : interfaces) {
    if (iface.number != control || iface.alternate != 0) continue;
    if (iface.klass != 2 || iface.subclass != 2) {
      error = "selected interface is not CDC ACM control"; return false;
    }
    for (std::size_t pos = 0; pos < iface.extra.size();) {
      const auto size = iface.extra[pos];
      if (size < 2 || pos + size > iface.extra.size()) {
        error = "malformed USB class descriptor"; return false;
      }
      if (size >= 3 && iface.extra[pos + 1] == 0x24 && iface.extra[pos + 2] == 6) {
        if (size != 5 || iface.extra[pos + 3] != control || data >= 0) {
          error = "ambiguous CDC Union descriptor"; return false;
        }
        data = iface.extra[pos + 4];
      }
      pos += size;
    }
  }
  if (data < 0 || data == control) { error = "CDC Union data interface missing"; return false; }
  int matches = 0;
  for (const auto& iface : interfaces) {
    if (iface.number != data || iface.klass != 10) continue;
    CdcEndpoints candidate{control, data, iface.alternate};
    int inputs = 0, outputs = 0;
    for (const auto& ep : iface.endpoints) {
      if ((ep.attributes & 3) != 2) continue;
      if ((ep.address & 15) == 0 || ep.packet_size < 1 || ep.packet_size > 1024) {
        error = "invalid bulk endpoint"; return false;
      }
      if (ep.address & 0x80) { candidate.input = ep.address; candidate.packet_size = ep.packet_size; ++inputs; }
      else { candidate.output = ep.address; ++outputs; }
    }
    if (inputs == 1 && outputs == 1) { selected = candidate; ++matches; }
    else if (inputs || outputs) { error = "CDC data interface needs one bulk IN and OUT"; return false; }
  }
  if (matches != 1) { error = "missing or ambiguous CDC bulk alternate setting"; return false; }
  return true;
}

namespace {
class UsbCdcPort final : public SerialPort {
 public:
  UsbCdcPort(UsbCdcConfig config, std::unique_ptr<UsbBackend> backend)
      : config_(std::move(config)), backend_(std::move(backend)) {
    if (config_.control_interfaces.empty()) config_.control_interfaces = {0};
    name_ = "libusb CDC control=" + std::to_string(current_control());
  }
  ~UsbCdcPort() override { close(); }
  bool open() override {
    if (open_) return true;
    error_.clear(); pending_.clear();
    if (!backend_ || config_.baud == 0 || config_.timeout_ms == 0 || config_.timeout_ms > 2000) {
      error_ = "invalid USB CDC configuration"; return false;
    }
    std::vector<UsbInterface> interfaces;
    if (!backend_->open(config_, interfaces, error_)) { backend_->close(); return false; }
    if (!select_cdc_endpoints(interfaces, current_control(), endpoints_, error_)) {
      close(); return false;
    }
    for (int iface : {endpoints_.control, endpoints_.data}) {
      const int active = backend_->kernel_active(iface);
      if (active != 0) { error_ = "USB interface unavailable or has a bound kernel driver; refusing detach"; close(); return false; }
      const int rc = backend_->claim(iface);
      if (rc < 0) { fail("claim interface", rc); return false; }
      claimed_.push_back(iface);
    }
    int rc = backend_->alternate(endpoints_.data, endpoints_.alternate);
    if (rc < 0) { fail("set interface alternate", rc); return false; }
    // CDC SET_LINE_CODING: little-endian baud, one stop bit, no parity, 8 data bits.
    std::array<unsigned char, 7> coding{
        static_cast<unsigned char>(config_.baud), static_cast<unsigned char>(config_.baud >> 8),
        static_cast<unsigned char>(config_.baud >> 16), static_cast<unsigned char>(config_.baud >> 24), 0, 0, 8};
    rc = backend_->control(0x20, 0, endpoints_.control, coding.data(), coding.size(), config_.timeout_ms);
    if (rc != 7) { fail("SET_LINE_CODING", rc); return false; }
    // Force a fresh session edge even after an App died with DTR asserted.
    // Set the normal baud first so this never performs a 1200-baud reset.
    rc = backend_->control(0x22, 0, endpoints_.control, nullptr, 0, config_.timeout_ms);
    if (rc != 0) { fail("clear DTR/RTS", rc); return false; }
    rc = backend_->control(0x22, 3, endpoints_.control, nullptr, 0, config_.timeout_ms);
    if (rc != 0) { fail("SET_CONTROL_LINE_STATE (DTR/RTS)", rc); return false; }
    dtr_ = true; open_ = true;
    name_ = "libusb CDC control=" + std::to_string(endpoints_.control) +
            " data=" + std::to_string(endpoints_.data) + " in=" + std::to_string(endpoints_.input) +
            " out=" + std::to_string(endpoints_.output);
    return true;
  }
  void close() override {
    if (!backend_) return;
    if (dtr_) backend_->control(0x22, 0, endpoints_.control, nullptr, 0, config_.timeout_ms);
    dtr_ = false;
    for (auto it = claimed_.rbegin(); it != claimed_.rend(); ++it) backend_->release(*it);
    claimed_.clear(); backend_->close(); open_ = false; pending_.clear();
  }
  bool is_open() const override { return open_; }
  bool write(const std::string& data) override {
    if (!open_) { error_ = "USB write on closed link"; return false; }
    if (data.empty() || data.size() > 4096) { error_ = "USB command size outside 1..4096"; return false; }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(config_.timeout_ms);
    std::size_t sent = 0;
    while (sent < data.size()) {
      const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
      if (remaining <= 0) { fail("USB write timeout (outcome unknown)", -7); return false; }
      int transferred = 0;
      const int rc = backend_->bulk(endpoints_.output,
          reinterpret_cast<unsigned char*>(const_cast<char*>(data.data() + sent)),
          static_cast<int>(data.size() - sent), transferred, static_cast<unsigned int>(remaining));
      if (rc < 0 || transferred <= 0 || static_cast<std::size_t>(transferred) > data.size() - sent) {
        fail("USB write failed (outcome unknown; no retry)", rc); return false;
      }
      sent += transferred;
    }
    return true;
  }
  std::string read(std::size_t max_bytes, int timeout_ms) override {
    if (!open_) { error_ = "USB read on closed link"; return {}; }
    if (max_bytes == 0 || max_bytes > 65536 || timeout_ms < 0 || timeout_ms > 2000) {
      error_ = "invalid USB read bounds"; return {};
    }
    if (pending_.empty()) {
      // A multiple of wMaxPacketSize avoids LIBUSB_ERROR_OVERFLOW on short caller buffers.
      const auto packet = static_cast<std::size_t>(endpoints_.packet_size);
      std::vector<unsigned char> data(((max_bytes + packet - 1) / packet) * packet);
      int transferred = 0;
      const int rc = backend_->bulk(endpoints_.input, data.data(), static_cast<int>(data.size()),
                                   transferred, static_cast<unsigned int>(std::max(1, timeout_ms)));
      if ((rc < 0 && rc != -7) || transferred < 0 || static_cast<std::size_t>(transferred) > data.size()) {
        fail("USB read", rc); return {};
      }
      // libusb may deliver valid partial bytes together with a timeout.
      pending_.assign(reinterpret_cast<const char*>(data.data()), static_cast<std::size_t>(transferred));
    }
    auto result = pending_.substr(0, max_bytes); pending_.erase(0, result.size()); return result;
  }
  const std::string& name() const override { return name_; }
  const std::string& error() const override { return error_; }
  bool select_next_candidate() override {
    // Advance to the next CDC-ACM control interface, if any remains. The caller
    // (worker) uses this after a claimed-but-unresponsive channel: the OpenRB
    // exposes two CDCs and the firmware's command channel is not identifiable
    // from descriptors, so we try each until one answers the handshake.
    if (candidate_index_ + 1 >= config_.control_interfaces.size()) return false;
    if (open_) close();
    ++candidate_index_;
    name_ = "libusb CDC control=" + std::to_string(current_control());
    return true;
  }
  void reset_candidate() override {
    // Start the next connect from the first candidate. Without this, a previous
    // failed connect leaves candidate_index_ at the last interface, so a
    // reconnect would only ever try that one -- the "worked once, never
    // reconnects" failure. Reopen from a clean slate every attempt.
    if (candidate_index_ != 0) {
      if (open_) close();
      candidate_index_ = 0;
      name_ = "libusb CDC control=" + std::to_string(current_control());
    }
  }
 private:
  int current_control() const {
    return config_.control_interfaces.empty() ? 0 : config_.control_interfaces[candidate_index_];
  }
  void fail(const std::string& operation, int rc) { error_ = operation + ": " + backend_->describe(rc); close(); }
  UsbCdcConfig config_;
  std::unique_ptr<UsbBackend> backend_;
  CdcEndpoints endpoints_;
  std::vector<int> claimed_;
  std::size_t candidate_index_ = 0;
  bool open_ = false, dtr_ = false;
  std::string name_, error_, pending_;
};
}  // namespace
std::unique_ptr<SerialPort> make_usb_cdc_port(UsbCdcConfig config, std::unique_ptr<UsbBackend> backend) {
  return std::make_unique<UsbCdcPort>(std::move(config), std::move(backend));
}
}  // namespace scifi2_hub::exo
