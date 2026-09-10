#include "serial_port.hpp"

#include <utility>

#if defined(__unix__) || defined(__APPLE__)
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <vector>
#endif

namespace scifi2_hub::exo {

namespace {

#if defined(__unix__) || defined(__APPLE__)

// Map a numeric baud to the termios speed constant. The exo runs at 1 Mbps by
// default; only the values this project uses are mapped, and an unmapped value
// is reported rather than silently misconfiguring the line.
bool baud_constant(unsigned int baud, speed_t& out) {
  switch (baud) {
    case 9600: out = B9600; return true;
    case 19200: out = B19200; return true;
    case 38400: out = B38400; return true;
    case 57600: out = B57600; return true;
    case 115200: out = B115200; return true;
    case 230400: out = B230400; return true;
    case 460800: out = B460800; return true;
    case 921600: out = B921600; return true;
    case 1000000: out = B1000000; return true;
    default: return false;
  }
}

// POSIX termios raw-mode serial port. One node, bidirectional: the OpenRB
// dual-CDC firmware defaults to reply_route:both, so a single node carries both
// commands out and replies in. (A second node for a split reply route can be
// added later behind the same SerialPort seam if the App path needs it.)
class PosixSerialPort : public SerialPort {
 public:
  PosixSerialPort(std::string device_path, unsigned int baud)
      : device_path_(std::move(device_path)), baud_(baud) {}

  ~PosixSerialPort() override { close(); }

  bool open() override {
    if (fd_ >= 0) return true;
    error_.clear();
    speed_t speed;
    if (!baud_constant(baud_, speed)) {
      error_ = "unsupported baud rate";
      return false;
    }
    const int fd = ::open(device_path_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
      error_ = std::string("open failed: ") + std::strerror(errno);
      return false;
    }
    termios tio{};
    if (::tcgetattr(fd, &tio) != 0) {
      error_ = std::string("tcgetattr failed: ") + std::strerror(errno);
      ::close(fd);
      return false;
    }
    cfmakeraw(&tio);
    tio.c_cflag |= (CLOCAL | CREAD);
    tio.c_cflag &= ~CRTSCTS;
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 0;  // non-blocking; poll() governs the read timeout.
    if (cfsetispeed(&tio, speed) != 0 || cfsetospeed(&tio, speed) != 0) {
      error_ = std::string("cfsetspeed failed: ") + std::strerror(errno);
      ::close(fd);
      return false;
    }
    if (::tcsetattr(fd, TCSANOW, &tio) != 0) {
      error_ = std::string("tcsetattr failed: ") + std::strerror(errno);
      ::close(fd);
      return false;
    }
    ::tcflush(fd, TCIOFLUSH);
    fd_ = fd;
    return true;
  }

  void close() override {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }

  bool is_open() const override { return fd_ >= 0; }

  bool write(const std::string& data) override {
    if (fd_ < 0) {
      error_ = "write on closed port";
      return false;
    }
    std::size_t written = 0;
    while (written < data.size()) {
      const ssize_t n = ::write(fd_, data.data() + written, data.size() - written);
      if (n < 0) {
        if (errno == EAGAIN || errno == EINTR) continue;
        error_ = std::string("write failed: ") + std::strerror(errno);
        return false;
      }
      written += static_cast<std::size_t>(n);
    }
    return true;
  }

  std::string read(std::size_t max_bytes, int timeout_ms) override {
    if (fd_ < 0) {
      error_ = "read on closed port";
      return {};
    }
    pollfd pfd{fd_, POLLIN, 0};
    const int ready = ::poll(&pfd, 1, timeout_ms);
    if (ready <= 0) return {};  // timeout (0) or interrupted (<0): no bytes.
    std::vector<char> buffer(max_bytes);
    const ssize_t n = ::read(fd_, buffer.data(), buffer.size());
    if (n <= 0) return {};
    return std::string(buffer.data(), static_cast<std::size_t>(n));
  }

  const std::string& name() const override { return device_path_; }
  const std::string& error() const override { return error_; }

 private:
  std::string device_path_;
  unsigned int baud_;
  int fd_ = -1;
  std::string error_;
};

#endif  // POSIX

// Fallback used on platforms without a POSIX tty (e.g. a Windows host build of
// the App code). It never opens; the App logs the failure and runs with the
// exo link down, so nothing else needs a platform guard.
class UnsupportedSerialPort : public SerialPort {
 public:
  explicit UnsupportedSerialPort(std::string device_path)
      : device_path_(std::move(device_path)),
        error_("serial ports are not supported on this platform") {}

  bool open() override { return false; }
  void close() override {}
  bool is_open() const override { return false; }
  bool write(const std::string&) override { return false; }
  std::string read(std::size_t, int) override { return {}; }
  const std::string& name() const override { return device_path_; }
  const std::string& error() const override { return error_; }

 private:
  std::string device_path_;
  std::string error_;
};

}  // namespace

std::unique_ptr<SerialPort> make_platform_serial_port(std::string device_path,
                                                      unsigned int baud) {
#if defined(__unix__) || defined(__APPLE__)
  return std::make_unique<PosixSerialPort>(std::move(device_path), baud);
#else
  (void)baud;
  return std::make_unique<UnsupportedSerialPort>(std::move(device_path));
#endif
}

}  // namespace scifi2_hub::exo
