#pragma once

#include <cstddef>
#include <memory>
#include <string>

namespace scifi2_hub::exo {

// Abstract byte-stream serial link the exo worker writes commands to and reads
// replies from. Kept deliberately small and SDK-independent so the worker can
// be exercised against an in-process fake with no hardware, and so the concrete
// device path (a raw /dev/ttyACM node today, an app-sdk serial API later) can
// be swapped without touching the worker. The device path/name is supplied from
// config as a plain parameter -- see ExoLinkConfig::device_path.
class SerialPort {
 public:
  virtual ~SerialPort() = default;

  // Open the link. Returns false (and sets error()) on failure; a failed open
  // leaves the port closed. Idempotent: opening an already-open port is a
  // success that changes nothing.
  virtual bool open() = 0;

  // Close the link. Safe to call when already closed.
  virtual void close() = 0;

  virtual bool is_open() const = 0;

  // Write all of `data`. Returns false on a short write or error; the worker
  // treats a false as a link failure. `data` is a complete framed command
  // including its line terminator.
  virtual bool write(const std::string& data) = 0;

  // Read whatever bytes are available, up to `max_bytes`, waiting at most
  // `timeout_ms` for the first byte. Returns the bytes read (possibly empty on
  // timeout). A read on a closed or failed port returns empty and sets error().
  virtual std::string read(std::size_t max_bytes, int timeout_ms) = 0;

  // Human-readable identifier for logs (e.g. the device path).
  virtual const std::string& name() const = 0;

  // Last error string, empty when none.
  virtual const std::string& error() const = 0;
};

// Factory for the platform serial port. On POSIX this opens a termios raw-mode
// tty; elsewhere it returns a port whose open() fails with a clear message so
// the App still builds and runs (exo simply never links). Defined in
// serial_port.cpp.
std::unique_ptr<SerialPort> make_platform_serial_port(std::string device_path,
                                                      unsigned int baud);

}  // namespace scifi2_hub::exo
