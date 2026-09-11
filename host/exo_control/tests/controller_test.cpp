#include "exo_control/controller.hpp"
#include "exo_control/exo.h"
#include <deque>
#include <iostream>
#include <thread>
using namespace exo_control;
namespace {
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void throws(F fn) { bool caught=false; try { fn(); } catch(const std::exception&) { caught=true; } check(caught,"expected failure"); }
struct Fake : Transport {
  std::deque<std::string> results, states;
  std::vector<wire::ControlCommand> sent;
  bool opened=false, drop=false, reject=false, wrong=false, fail_open=false, malformed=false, drop_state=false, wrong_id=false;
  std::chrono::steady_clock::time_point delay_until{};
  void open(Milliseconds) override { opened=true; if(fail_open) throw std::runtime_error("open failure"); }
  void close() noexcept override { opened=false; }
  void send(const std::string& bytes) override {
    wire::ControlCommand c; check(c.ParseFromString(bytes),"command parse");
    check(c.protocol_version()==1 && !c.request_id().empty(),"envelope");
    sent.push_back(c);
    if(drop) return;
    if(c.command()==wire::COMMAND_GET_STATE && !drop_state) {
      wire::StateSnapshot snapshot; snapshot.set_protocol_version(1);
      snapshot.set_timestamp_ns(123);
      states.push_back(snapshot.SerializeAsString());
    }
    wire::CommandResult r;
    r.set_protocol_version(1); r.set_request_id(c.request_id()); r.set_command(c.command());
    r.set_status(wire::RESULT_ACCEPTED); results.push_back(r.SerializeAsString());
    r.set_status(reject ? wire::RESULT_FAILED : wire::RESULT_SUCCEEDED);
    if(reject) { r.mutable_error()->set_code(wire::ERROR_INVALID_ARGUMENT); r.mutable_error()->set_message("motion disabled"); }
    if(wrong) r.set_command(wire::COMMAND_FIT);
    if(wrong_id) r.set_request_id("another-client/request");
    results.push_back(malformed ? std::string("broken") : r.SerializeAsString());
  }
  std::optional<std::string> receive(bool state, Milliseconds wait) override {
    if (!state && std::chrono::steady_clock::now() < delay_until) {
      std::this_thread::sleep_for(wait); return {};
    }
    auto& q=state ? states : results;
    if(q.empty()) { std::this_thread::sleep_for(wait); return {}; }
    auto value=q.front(); q.pop_front(); return value;
  }
};
struct Fixture {
  Fake* fake;
  Controller c;
  Fixture() : Fixture(std::make_unique<Fake>()) {}
  explicit Fixture(std::unique_ptr<Fake> f) : fake(f.get()), c(std::move(f),Milliseconds(30)) {}
};
void commands() {
  Fixture f; f.c.connect();
  check(f.fake->sent.size()==2,"handshake + subscription");
  f.c.set_mode(wire::EXO_MODE_CONNECTED);
  f.c.set_mode(wire::EXO_MODE_EXTERNAL);
  f.c.set_pose({{wire::EXO_JOINT_THUMB,-100},{wire::EXO_JOINT_WRIST,100}});
  const auto& pose=f.fake->sent.back().set_exo_pose();
  check(pose.joints_size()==2 && pose.joints(0).value()==-100,"signed pose");
  f.c.query("version"); f.c.raw("get_enable:all");
  f.c.disconnect();
  check(!f.fake->opened && f.fake->sent.back().set_exo_mode().mode()==wire::EXO_MODE_OFF,"OFF precedes close");
}
void validation() {
  Fixture f;
  throws([&]{f.c.set_mode(wire::EXO_MODE_EXTERNAL);});
  f.c.connect(); auto n=f.fake->sent.size();
  throws([&]{f.c.set_pose({});});
  throws([&]{f.c.set_pose({{wire::EXO_JOINT_INDEX,101}});});
  throws([&]{f.c.set_mode(wire::EXO_MODE_UNSPECIFIED);});
  throws([&]{f.c.query("reboot:all");});
  for(const auto& s : {std::string(""),std::string(" \t"),std::string("x\ny"),std::string(201,'x'),std::string("a\0b",3)})
    throws([&]{f.c.raw(s);});
  check(f.fake->sent.size()==n,"invalid input sent nothing");
  f.c.disconnect(); check(f.fake->sent.size()==n,"unsent pre-connect mode does not acquire Exo ownership");
}
void errors_and_teardown() {
  Fixture f; f.c.connect(); f.fake->reject=true;
  bool rejected=false;
  try { f.c.set_mode(wire::EXO_MODE_EXTERNAL); }
  catch(const CommandError& e) { rejected=e.result.error().message()=="motion disabled"; }
  check(rejected,"device rejection retained");
  throws([&]{f.c.disconnect();}); check(!f.fake->opened,"close after failed OFF");
  Fixture t; t.c.connect(); t.fake->drop=true;
  auto before=t.fake->sent.size();
  throws([&]{t.c.set_mode(wire::EXO_MODE_CONNECTED);});
  check(t.fake->sent.size()==before+1,"no retry after timeout");
  throws([&]{t.c.disconnect();});
  check(t.fake->sent.back().set_exo_mode().mode()==wire::EXO_MODE_OFF && !t.fake->opened,"unknown outcome disarm");
  Fixture w; w.c.connect(); w.fake->wrong=true; throws([&]{w.c.query("version");});
  Fixture id; id.c.connect(); id.fake->wrong_id=true; throws([&]{id.c.query("version");});
  Fixture m; m.c.connect(); m.fake->malformed=true; throws([&]{m.c.query("version");});
  Fixture o; o.fake->fail_open=true; throws([&]{o.c.connect();}); check(!o.fake->opened,"partial open cleaned");
  Fixture s; s.fake->drop_state=true; throws([&]{s.c.connect();}); check(!s.fake->opened,"missing initial state fails connect");
}
void snapshots() {
  Fixture f; f.c.connect(); int callbacks=0;
  f.c.on_state([&](const auto& s){check(s.timestamp_ns()==123,"source timestamp retained"); ++callbacks;});
  wire::StateSnapshot state; state.set_protocol_version(1); state.set_state_version(5); state.set_timestamp_ns(123);
  state.mutable_exo()->set_last_reply("firmware text");
  f.fake->states.push_back(state.SerializeAsString()); f.c.poll();
  state.set_state_version(4); f.fake->states.push_back(state.SerializeAsString()); f.c.poll();
  check(callbacks==1 && f.c.state()->exo().last_reply()=="firmware text","snapshot + stale drop");
  f.c.disconnect(); f.c.connect();
  auto before=callbacks;
  f.fake->states.push_back(state.SerializeAsString()); f.c.poll(); check(callbacks==before+1,"reconnect clears version");
}
void slow_handshake() {
  auto transport=std::make_unique<Fake>(); auto* f=transport.get();
  f->delay_until=std::chrono::steady_clock::now()+Milliseconds(170);
  Controller c(std::move(transport),Milliseconds(400)); c.connect();
  check(c.connected() && f->sent.size()>=3,"delayed handshake reply survives subsequent read-only retry");
}
void c_abi() {
  check(exo_abi_version()==1,"ABI version");
  exo_client* c=nullptr;
  check(exo_create("127.0.0.1",647,30,&c)==EXO_OK,"C create without network");
  check(exo_query(c,"forbidden")==EXO_INVALID_ARGUMENT,"C validation");
  size_t size=0; check(exo_copy_error(c,nullptr,0,&size)==EXO_BUFFER_TOO_SMALL && size>1,"error sizing");
  char b[2]={'x','y'};
  check(exo_copy_error(c,b,2,&size)==EXO_BUFFER_TOO_SMALL && b[0]=='x',"no partial write");
  check(exo_copy_state_json(c,nullptr,0,&size)==EXO_BUFFER_TOO_SMALL && size==5,"no snapshot is null");
  int j[]={1,1},v[]={0,0}; check(exo_set_pose(c,j,v,2)==EXO_INVALID_ARGUMENT,"duplicate C joints");
  check(exo_disconnect(c)==EXO_OK,"disconnected cleanup"); exo_destroy(c); exo_destroy(nullptr);
}
}
int main() { try { commands(); validation(); errors_and_teardown(); snapshots(); slow_handshake(); c_abi(); std::cout<<"All Exo controller tests passed\n"; return 0; }
  catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; } }
