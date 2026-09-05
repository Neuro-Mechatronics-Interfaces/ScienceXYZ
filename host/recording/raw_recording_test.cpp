#include "hdf5_record_sink.hpp"
#include "api/datatype.pb.h"
#include <hdf5.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>

#ifdef TEST_HDF5_FAILURES
bool fail_write = false, fail_flush = false;
extern "C" herr_t __real_H5Dwrite(hid_t, hid_t, hid_t, hid_t, hid_t, const void*);
extern "C" herr_t __wrap_H5Dwrite(hid_t d, hid_t t, hid_t m, hid_t f, hid_t p, const void* b) {
  return fail_write ? -1 : __real_H5Dwrite(d, t, m, f, p, b);
}
extern "C" herr_t __real_H5Fflush(hid_t, H5F_scope_t);
extern "C" herr_t __wrap_H5Fflush(hid_t f, H5F_scope_t s) {
  return fail_flush ? -1 : __real_H5Fflush(f, s);
}
#endif

void check(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

int main(int argc, char** argv) {
  try {
    check(argc == 2, "expected output path");
    const std::filesystem::path path(argv[1]);
    // Only this test-owned output is replaced, never a recording supplied to CLI.
    std::filesystem::remove(path);
    synapse::BroadbandFrame frame;
    frame.set_timestamp_ns(123456789);
    frame.set_unix_timestamp_ns(987654321);
    frame.set_sequence_number(42);
    frame.set_sample_rate_hz(20000);
    for (int n = 0; n < 34; ++n) frame.add_frame_data(n - 17);
    auto* electrodes = frame.add_channel_ranges();
    electrodes->set_type(synapse::ChannelType::ELECTRODE);
    electrodes->set_count(32);
    auto* gpio = frame.add_channel_ranges();
    gpio->set_type(synapse::ChannelType::GPIO);
    gpio->set_count(2);
    gpio->add_channel_ids(0);
    gpio->add_channel_ids(1);
    std::string bytes = frame.SerializeAsString();
    bytes += std::string("\x98\x06\x07", 3); // Unknown field 99, preserved verbatim.
    app::recording::RawTapMessage message{555, {bytes.begin(), bytes.end()}};
    {
      app::recording::Hdf5RecordSink sink(path.string());
      check(sink.open("test", 111, "{}"), "open");
      check(sink.write_raw_messages(true, {}), "empty batch");
      check(sink.write_raw_messages(true, {message}), "first append");
      check(sink.write_raw_messages(true, {message, {556, {0xff}}, {557, {}}}), "second append incl malformed and empty wire");
      check(sink.write_raw_messages(false, {{558, {0, 1, 0, 2}}}), "task raw binary");
      check(sink.write_recording_status("{\"state\":\"stopped\",\"tail_complete\":false}"), "status");
      check(sink.close(), "close");
      check(!sink.write_raw_messages(true, {message}), "write after close rejected");
    }
    const auto size = std::filesystem::file_size(path);
    {
      app::recording::Hdf5RecordSink collision(path.string());
      H5Eset_auto2(H5E_DEFAULT, nullptr, nullptr);
      check(!collision.open("test", 0, "overwrite"), "existing file refused");
    }
    check(std::filesystem::file_size(path) == size, "existing file size unchanged");
    hid_t file = H5Fopen(path.string().c_str(), H5F_ACC_RDONLY, H5P_DEFAULT);
    check(file >= 0, "reopen");
    hid_t dataset = H5Dopen2(file, "/raw_broadband", H5P_DEFAULT);
    check(dataset >= 0, "raw dataset");
    hid_t space = H5Dget_space(dataset);
    check(H5Sget_simple_extent_npoints(space) == 4, "append row count");
    struct Row { std::uint64_t host_receive_time_ns; hvl_t payload; } rows[4]{};
    hid_t type = H5Dget_type(dataset);
    check(H5Dread(dataset, type, H5S_ALL, H5S_ALL, H5P_DEFAULT, rows) >= 0, "read raw");
    check(rows[0].host_receive_time_ns == 555, "receipt timestamp");
    std::string restored(static_cast<const char*>(rows[0].payload.p), rows[0].payload.len);
    check(restored == bytes, "exact wire bytes including unknown fields");
    synapse::BroadbandFrame decoded;
    check(decoded.ParseFromString(restored), "decode persisted frame");
    check(decoded.frame_data_size() == 34 && decoded.frame_data(0) == -17 &&
          decoded.channel_ranges(1).channel_ids(1) == 1 &&
          decoded.timestamp_ns() == 123456789 && decoded.unix_timestamp_ns() == 987654321 &&
          decoded.sample_rate_hz() == 20000 && decoded.sequence_number() == 42, "sample metadata preserved");
    check(rows[2].payload.len == 1 && rows[3].payload.len == 0, "malformed/empty retained");
    check(H5Dvlen_reclaim(type, space, H5P_DEFAULT, rows) >= 0, "reclaim");
    H5Tclose(type); H5Sclose(space); H5Dclose(dataset); H5Fclose(file);
#ifdef TEST_HDF5_FAILURES
    for (bool write : {true, false}) {
      const std::string failure_path = path.string() + (write ? ".write-failure" : ".flush-failure");
      std::filesystem::remove(failure_path);
      app::recording::Hdf5RecordSink sink(failure_path);
      check(sink.open("test", 0, "{}"), "failure fixture open");
      fail_write = write; fail_flush = !write;
      check(!sink.write_raw_messages(true, {message}), "I/O failure surfaced");
      fail_write = false; fail_flush = false;
      check(sink.close(), "failure fixture close");
    }
#endif
    std::cout << "raw HDF5 roundtrip and overwrite protection passed\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
