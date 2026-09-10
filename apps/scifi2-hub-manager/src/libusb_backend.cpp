#include "usb_cdc_port.hpp"
#include <libusb.h>
#include <spdlog/spdlog.h>
#include <array>
#include <utility>

namespace scifi2_hub::exo {
namespace {
class LibusbBackend final : public UsbBackend {
 public:
  ~LibusbBackend() override { close(); }
  bool open(const UsbCdcConfig& config, std::vector<UsbInterface>& interfaces, std::string& error) override {
    close();
    int rc = libusb_init(&context_);
    if (rc < 0) { error = describe(rc); return false; }
    libusb_device** list = nullptr;
    const auto count = libusb_get_device_list(context_, &list);
    if (count < 0) { error = describe(static_cast<int>(count)); close(); return false; }
    int matches = 0;
    bool inaccessible = false;
    for (ssize_t i = 0; i < count; ++i) {
      libusb_device_descriptor descriptor{};
      if (libusb_get_device_descriptor(list[i], &descriptor) != 0 ||
          descriptor.idVendor != config.vendor || descriptor.idProduct != config.product) continue;
      libusb_device_handle* candidate = nullptr;
      rc = libusb_open(list[i], &candidate);
      if (rc < 0) { inaccessible = true; error = "matching USB device cannot open: " + describe(rc); continue; }
      if (!config.serial.empty()) {
        std::array<unsigned char, 256> serial{};
        const int n = descriptor.iSerialNumber ? libusb_get_string_descriptor_ascii(candidate, descriptor.iSerialNumber, serial.data(), serial.size()) : 0;
        if (n < 0) { inaccessible = true; libusb_close(candidate); continue; }
        if (std::string(reinterpret_cast<char*>(serial.data()), n) != config.serial) { libusb_close(candidate); continue; }
      }
      ++matches;
      if (!handle_) handle_ = candidate; else libusb_close(candidate);
    }
    libusb_free_device_list(list, 1);
    if (matches != 1 || inaccessible) {
      error = inaccessible ? "USB identity selection incomplete: a matching device is inaccessible" :
              (matches == 0 ? "no matching USB device" : "multiple matching USB devices; configure exo_usb_serial");
      close(); return false;
    }
    libusb_config_descriptor* descriptor = nullptr;
    rc = libusb_get_active_config_descriptor(libusb_get_device(handle_), &descriptor);
    if (rc < 0) { error = "active USB configuration: " + describe(rc); close(); return false; }
    for (int i = 0; i < descriptor->bNumInterfaces; ++i) {
      const auto& iface = descriptor->interface[i];
      for (int j = 0; j < iface.num_altsetting; ++j) {
        const auto& alt = iface.altsetting[j];
        UsbInterface out;
        out.number = alt.bInterfaceNumber; out.alternate = alt.bAlternateSetting;
        out.klass = alt.bInterfaceClass; out.subclass = alt.bInterfaceSubClass;
        if (alt.extra_length > 0) out.extra.assign(alt.extra, alt.extra + alt.extra_length);
        for (int k = 0; k < alt.bNumEndpoints; ++k) {
          const auto& ep = alt.endpoint[k];
          out.endpoints.push_back({ep.bEndpointAddress, ep.bmAttributes, ep.wMaxPacketSize & 0x7ff});
        }
        spdlog::info("Exo USB interface={} alt={} class={} subclass={} endpoints={}",
                     out.number, out.alternate, out.klass, out.subclass, out.endpoints.size());
        interfaces.push_back(std::move(out));
      }
    }
    libusb_free_config_descriptor(descriptor);
    return true;
  }
  void close() override {
    if (handle_) { libusb_close(handle_); handle_ = nullptr; }
    if (context_) { libusb_exit(context_); context_ = nullptr; }
  }
  int kernel_active(int iface) override { return libusb_kernel_driver_active(handle_, iface); }
  int claim(int iface) override { return libusb_claim_interface(handle_, iface); }
  void release(int iface) override { libusb_release_interface(handle_, iface); }
  int alternate(int iface, int alt) override { return libusb_set_interface_alt_setting(handle_, iface, alt); }
  int control(int request, int value, int iface, unsigned char* data, int length, unsigned int timeout) override {
    return libusb_control_transfer(handle_, 0x21, request, value, iface, data, length, timeout);
  }
  int bulk(int ep, unsigned char* data, int length, int& transferred, unsigned int timeout) override {
    return libusb_bulk_transfer(handle_, ep, data, length, &transferred, timeout);
  }
  std::string describe(int rc) const override { return std::string(libusb_error_name(rc)) + " (" + std::to_string(rc) + ")"; }
 private:
  libusb_context* context_ = nullptr;
  libusb_device_handle* handle_ = nullptr;
};
}  // namespace
std::unique_ptr<SerialPort> make_libusb_cdc_port(UsbCdcConfig config) {
  return make_usb_cdc_port(std::move(config), std::make_unique<LibusbBackend>());
}
}  // namespace scifi2_hub::exo
