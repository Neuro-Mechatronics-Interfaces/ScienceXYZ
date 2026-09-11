#include "exo_control/exo.h"
#include "exo_control/controller.hpp"
#include <google/protobuf/util/json_util.h>
#include <cstring>

struct exo_client {
  exo_control::Controller controller;
  std::string error, result;
  exo_client(const char* host, int port, int timeout)
    : controller(exo_control::make_synapse_transport(host, port), exo_control::Milliseconds(timeout)) {}
};
namespace {
std::string json(const google::protobuf::Message& value) {
  std::string out;
  if (!google::protobuf::util::MessageToJsonString(value, &out).ok())
    throw std::runtime_error("protobuf JSON conversion failed");
  return out;
}
template<class F> int call(exo_client* c, F fn) noexcept {
  if (!c) return EXO_INVALID_ARGUMENT;
  try { c->error.clear(); c->result.clear(); fn(); return EXO_OK; }
  catch (const exo_control::CommandError& e) {
    try { c->error = e.what(); c->result = json(e.result); } catch (...) {}
    return EXO_DEVICE_REJECTED;
  }
  catch (const std::invalid_argument& e) { try { c->error = e.what(); } catch (...) {} return EXO_INVALID_ARGUMENT; }
  catch (const exo_control::TimeoutError& e) { try { c->error = e.what(); } catch (...) {} return EXO_TIMEOUT; }
  catch (const std::exception& e) { try { c->error = e.what(); } catch (...) {} return EXO_FAILURE; }
  catch (...) { return EXO_FAILURE; }
}
int copy(const std::string& text, char* buffer, size_t capacity, size_t* required) noexcept {
  if (!required || (!buffer && capacity)) return EXO_INVALID_ARGUMENT;
  *required = text.size() + 1;
  if (!buffer || capacity < *required) return EXO_BUFFER_TOO_SMALL;
  std::memcpy(buffer, text.c_str(), *required);
  return EXO_OK;
}
}
extern "C" {
uint32_t exo_abi_version() { return 1; }
int exo_create(const char* host, int port, int timeout, exo_client** out) {
  if (!out) return EXO_INVALID_ARGUMENT;
  *out = nullptr;
  if (!host) return EXO_INVALID_ARGUMENT;
  try { *out = new exo_client(host, port, timeout); return EXO_OK; }
  catch (const std::invalid_argument&) { return EXO_INVALID_ARGUMENT; }
  catch (...) { return EXO_FAILURE; }
}
int exo_connect(exo_client* c) { return call(c, [&] { c->controller.connect(); }); }
int exo_disconnect(exo_client* c) { return call(c, [&] { c->controller.disconnect(); }); }
void exo_destroy(exo_client* c) { delete c; }
int exo_set_mode(exo_client* c, int mode) {
  return call(c, [&] { c->result = json(c->controller.set_mode(static_cast<exo_control::wire::ExoMode>(mode))); });
}
int exo_set_pose(exo_client* c, const int* joints, const int* values, size_t count) {
  return call(c, [&] {
    if (!joints || !values || count < 1 || count > 6) throw std::invalid_argument("pose requires 1..6 joints");
    std::map<exo_control::wire::ExoJoint, int> pose;
    for (size_t i=0; i<count; ++i)
      if (!pose.emplace(static_cast<exo_control::wire::ExoJoint>(joints[i]), values[i]).second)
        throw std::invalid_argument("duplicate joint");
    c->result = json(c->controller.set_pose(pose));
  });
}
int exo_query(exo_client* c, const char* query) {
  return call(c, [&] { if (!query) throw std::invalid_argument("null query"); c->result = json(c->controller.query(query)); });
}
int exo_raw(exo_client* c, const char* command) {
  return call(c, [&] { if (!command) throw std::invalid_argument("null command"); c->result = json(c->controller.raw(command)); });
}
int exo_get_state(exo_client* c) { return call(c, [&] { c->result = json(c->controller.get_state()); }); }
int exo_poll(exo_client* c, int timeout) { return call(c, [&] { c->controller.poll(exo_control::Milliseconds(timeout)); }); }
int exo_copy_error(exo_client* c, char* b, size_t n, size_t* r) { return c ? copy(c->error,b,n,r) : EXO_INVALID_ARGUMENT; }
int exo_copy_result_json(exo_client* c, char* b, size_t n, size_t* r) { return c ? copy(c->result,b,n,r) : EXO_INVALID_ARGUMENT; }
int exo_copy_state_json(exo_client* c, char* b, size_t n, size_t* r) {
  if (!c) return EXO_INVALID_ARGUMENT;
  try { return copy(c->controller.state() ? json(*c->controller.state()) : "null",b,n,r); }
  catch (...) { return EXO_FAILURE; }
}
}
