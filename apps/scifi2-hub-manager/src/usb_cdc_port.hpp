#pragma once

#include "serial_port.hpp"
#include <cstdint>
#include <vector>

namespace scifi2_hub::exo {

struct UsbCdcConfig {
  std::uint16_t vendor = 0x2f5d;
  std::uint16_t product = 0x2202;
  std::string serial;  // Empty permits exactly one matching device, never the first of many.
  int control_interface = 0;  // Primary command CDC, verified from its Union descriptor.
  unsigned int baud = 1000000;
  unsigned int timeout_ms = 500;
};

struct UsbEndpoint { int address; int attributes; int packet_size; };
struct UsbInterface {
  int number = 0, alternate = 0, klass = 0, subclass = 0;
  std::vector<unsigned char> extra;
  std::vector<UsbEndpoint> endpoints;
};
struct CdcEndpoints {
  int control = -1, data = -1, alternate = 0, input = 0, output = 0, packet_size = 0;
};

// SDK/libusb-independent topology validator and injectable I/O seam.
bool select_cdc_endpoints(const std::vector<UsbInterface>& interfaces, int control,
                          CdcEndpoints& selected, std::string& error);
class UsbBackend {
 public:
  virtual ~UsbBackend() = default;
  virtual bool open(const UsbCdcConfig&, std::vector<UsbInterface>&, std::string&) = 0;
  virtual void close() = 0;
  virtual int kernel_active(int interface) = 0;
  virtual int claim(int interface) = 0;
  virtual void release(int interface) = 0;
  virtual int alternate(int interface, int alternate) = 0;
  virtual int control(int request, int value, int interface, unsigned char* data,
                      int length, unsigned int timeout_ms) = 0;
  virtual int bulk(int endpoint, unsigned char* data, int length, int& transferred,
                   unsigned int timeout_ms) = 0;
  virtual std::string describe(int error) const = 0;
};

std::unique_ptr<SerialPort> make_usb_cdc_port(UsbCdcConfig, std::unique_ptr<UsbBackend>);
// Only the device App links this native factory. Hardware-free tests inject UsbBackend.
std::unique_ptr<SerialPort> make_libusb_cdc_port(UsbCdcConfig);

}  // namespace scifi2_hub::exo
