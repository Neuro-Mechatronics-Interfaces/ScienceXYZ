// Linux process integration fixture. Binds loopback only; never uses bench IPs.
#include "api/synapse.grpc.pb.h"
#include "api/datatype.pb.h"
#include "control_protocol.hpp"
#include <grpcpp/grpcpp.h>
#include <zmq.hpp>
#include <hdf5.h>
#include <spawn.h>
#include <sys/wait.h>
#include <signal.h>
#include <unistd.h>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <thread>
extern char** environ;
using namespace std::chrono_literals;
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

struct Fixture : synapse::SynapseDevice::Service {
  std::string reference, task;
  grpc::Status Query(grpc::ServerContext*, const synapse::QueryRequest* request,
                     synapse::QueryResponse* response) override {
    if (request->query_type() != synapse::QueryRequest::kListTaps)
      return {grpc::StatusCode::UNIMPLEMENTED, "fixture only lists taps"};
    for (bool ref : {false, true}) {
      auto* tap = response->mutable_list_taps_response()->add_taps();
      tap->set_name(ref ? "broadband_out" : "task_transition");
      tap->set_endpoint(ref ? reference : task);
      tap->set_tap_type(synapse::TAP_TYPE_PRODUCER);
    }
    return grpc::Status::OK;
  }
};
std::string read_status(hid_t file) {
  hid_t attribute = H5Aopen(file, "recording_status_json", H5P_DEFAULT);
  check(attribute >= 0, "status attribute");
  hid_t type = H5Aget_type(attribute);
  char* value = nullptr;
  check(H5Aread(attribute, type, &value) >= 0, "read status");
  std::string result(value);
  H5free_memory(value); H5Tclose(type); H5Aclose(attribute);
  return result;
}
int main(int argc, char** argv) {
  pid_t child = -1;
  try {
    check(argc == 4, "recorder executable, output path, stop|idle|loss required");
    const std::string mode = argv[3];
    std::filesystem::remove(argv[2]); // CTest-owned fixture output only.
    zmq::context_t context(1);
    zmq::socket_t reference(context, zmq::socket_type::xpub), task(context, zmq::socket_type::xpub);
    reference.bind("tcp://127.0.0.1:*"); task.bind("tcp://127.0.0.1:*");
    reference.set(zmq::sockopt::rcvtimeo, 5000); task.set(zmq::sockopt::rcvtimeo, 5000);
    Fixture fixture;
    fixture.reference = reference.get(zmq::sockopt::last_endpoint);
    fixture.task = task.get(zmq::sockopt::last_endpoint);
    int port = 0;
    grpc::ServerBuilder builder;
    builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials(), &port);
    builder.RegisterService(&fixture);
    auto server = builder.BuildAndStart();
    check(server != nullptr, "start loopback fixture");
    std::string uri = "127.0.0.1:" + std::to_string(port);
    std::vector<std::string> args{argv[1], "--device", uri, "--output", argv[2],
      "--idle-timeout-ms", "1200", "--metadata-json", "{\"fixture\":true}"};
    std::vector<char*> pointers;
    for (auto& arg : args) pointers.push_back(arg.data());
    pointers.push_back(nullptr);
    check(posix_spawn(&child, argv[1], nullptr, nullptr, pointers.data(), environ) == 0, "spawn recorder");
    zmq::message_t subscription;
    check(task.recv(subscription).has_value(), "task subscribed");
    check(reference.recv(subscription).has_value(), "reference subscribed");
    std::this_thread::sleep_for(100ms);
    constexpr int count = 2000;
    for (int n = 0; n < count; ++n) {
      synapse::BroadbandFrame frame;
      frame.set_sequence_number(n + (mode == "loss" && n >= 1000 ? 3 : 0));
      frame.set_timestamp_ns(1000000 + n * 50000);
      frame.set_unix_timestamp_ns(2000000 + n * 50000);
      frame.set_sample_rate_hz(20000);
      for (int ch = 0; ch < 34; ++ch) frame.add_frame_data(ch + n);
      check(reference.send(zmq::buffer(frame.SerializeAsString())).has_value(), "publish frame");
      if (n % 20 == 0) std::this_thread::sleep_for(1ms);
    }
    if (mode == "loss") {
      const std::string malformed(1, static_cast<char>(0xff));
      check(task.send(zmq::buffer(malformed)).has_value(), "publish bad task");
      check(reference.send(zmq::buffer(malformed)).has_value(), "publish bad reference");
    }
    if (mode == "stop") {
      using namespace stateful_decode_and_sync::v1;
      for (int n = 1; n <= 2; ++n) {
        TaskTransitionEvent event;
        event.set_protocol_version(app::protocol::kProtocolVersion);
        event.set_definition_id("fixture.task"); event.set_definition_revision(1);
        event.set_definition_hash(std::string(71, 'a'));
        event.set_app_session_id("fixture-session"); event.set_run_sequence(1);
        event.set_event_sequence(n); event.set_transition_sequence(n);
        event.set_event_kind(n == 1 ? TASK_EVENT_START : TASK_EVENT_ABORT);
        event.set_current_state_id(n == 1 ? 1 : 0);
        event.set_previous_state_id(n == 1 ? 0 : 1);
        event.set_trigger_kind(n == 1 ? TASK_TRIGGER_START_COMMAND : TASK_TRIGGER_ABORT_COMMAND);
        event.mutable_effective_frame()->set_source_id("rhd2132");
        event.mutable_effective_frame()->set_sequence_number(n * 100);
        event.mutable_effective_frame()->set_timestamp_ns(1000000 + n * 100 * 50000);
        check(task.send(zmq::buffer(event.SerializeAsString())).has_value(), "publish task commit");
      }
    }
    std::this_thread::sleep_for(300ms);
    if (mode != "idle") check(kill(child, SIGTERM) == 0, "stop child");
    int result = 0;
    bool exited = false;
    for (int n = 0; n < 100; ++n) {
      if (waitpid(child, &result, WNOHANG) == child) { exited = true; child = -1; break; }
      std::this_thread::sleep_for(50ms);
    }
    check(exited && WIFEXITED(result), "recorder exited within deadline");
    check(WEXITSTATUS(result) == (mode == "idle" ? 1 : 0), "recorder exit code");
    hid_t file = H5Fopen(argv[2], H5F_ACC_RDONLY, H5P_DEFAULT);
    check(file >= 0, "fresh process reopens recording");
    hid_t dataset = H5Dopen2(file, "/raw_broadband", H5P_DEFAULT);
    hid_t space = H5Dget_space(dataset);
    check(H5Sget_simple_extent_npoints(space) == count + (mode == "loss" ? 1 : 0), "all raw messages persisted while task idle");
    const auto status = read_status(file);
    check(status.find("\"tail_complete\":false") != std::string::npos, "honest stop tail");
    check(status.find(mode == "idle" ? "reference_idle_timeout" : "\"state\":\"stopped\"") != std::string::npos, "stop/failure reason");
    if (mode == "loss") {
      check(status.find("\"missing_reference_sequences\":3") != std::string::npos, "sequence gap count");
      check(status.find("\"task_parse_errors\":1") != std::string::npos, "task parse count");
      check(status.find("\"reference_parse_errors\":1") != std::string::npos, "reference parse count");
    }
    if (mode == "stop") {
      hid_t transitions = H5Dopen2(file, "/transitions", H5P_DEFAULT);
      hid_t transition_space = H5Dget_space(transitions);
      check(H5Sget_simple_extent_npoints(transition_space) == 2, "both committed task records persisted");
      struct Boundary { std::uint64_t sequence, time; } boundaries[2]{};
      hid_t projection = H5Tcreate(H5T_COMPOUND, sizeof(Boundary));
      H5Tinsert(projection, "effective_sequence_number", HOFFSET(Boundary, sequence), H5T_NATIVE_UINT64);
      H5Tinsert(projection, "effective_timestamp_ns", HOFFSET(Boundary, time), H5T_NATIVE_UINT64);
      check(H5Dread(transitions, projection, H5S_ALL, H5S_ALL, H5P_DEFAULT, boundaries) >= 0, "read authoritative boundaries");
      check(boundaries[0].sequence == 100 && boundaries[0].time == 6000000 &&
            boundaries[1].sequence == 200 && boundaries[1].time == 11000000, "boundaries retain source identity");
      H5Tclose(projection); H5Sclose(transition_space); H5Dclose(transitions);
    }
    H5Sclose(space); H5Dclose(dataset); H5Fclose(file);
    server->Shutdown();
    std::cout << "loopback " << mode << " passed\n";
    return 0;
  } catch (const std::exception& e) {
    if (child > 0) { kill(child, SIGKILL); waitpid(child, nullptr, 0); }
    std::cerr << e.what() << '\n'; return 1;
  }
}
