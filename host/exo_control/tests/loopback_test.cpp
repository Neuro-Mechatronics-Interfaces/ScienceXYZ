#include "exo_control/controller.hpp"
#include "exo_control/exo.h"
#include "api/synapse.grpc.pb.h"
#include <grpcpp/grpcpp.h>
#include <zmq.hpp>
#include <atomic>
#include <future>
#include <iostream>
#include <thread>
using namespace exo_control;
namespace {
void check(bool ok, const char* message) { if(!ok) throw std::runtime_error(message); }
struct Service : synapse::SynapseDevice::Service {
  std::vector<std::string> endpoints;
  std::atomic<int> control_type{synapse::TAP_TYPE_CONSUMER};
  std::atomic<int> output_type{synapse::TAP_TYPE_PRODUCER};
  grpc::Status Query(grpc::ServerContext*, const synapse::QueryRequest* request, synapse::QueryResponse* response) override {
    if(request->query_type()!=synapse::QueryRequest::kListTaps || !request->has_list_taps_query())
      return {grpc::StatusCode::INVALID_ARGUMENT,"only ListTaps permitted"};
    const char* names[]={"control","command_result","state"};
    for(int i=0;i<3;++i) {
      auto tap=response->mutable_list_taps_response()->add_taps();
      tap->set_name(names[i]); tap->set_endpoint(endpoints.at(i));
      tap->set_tap_type(static_cast<synapse::TapType>(i==0 ? control_type.load() : output_type.load()));
    }
    response->mutable_status()->set_code(synapse::kOk);
    return grpc::Status::OK;
  }
};
struct Peer {
  std::atomic<bool> stop{false};
  std::atomic<int> off{0};
  std::promise<std::vector<std::string>> ready;
  std::exception_ptr error;
  std::thread worker;
  Peer() : worker([this]{ run(); }) {}
  ~Peer() { stop=true; worker.join(); }
  void run() {
    try {
      zmq::context_t context(1);
      zmq::socket_t control(context,zmq::socket_type::sub), result(context,zmq::socket_type::pub), state(context,zmq::socket_type::pub);
      control.set(zmq::sockopt::subscribe,""); control.set(zmq::sockopt::rcvtimeo,10);
      for(auto s:{&control,&result,&state}) { s->set(zmq::sockopt::linger,0); s->bind("tcp://127.0.0.1:*"); }
      ready.set_value({control.get(zmq::sockopt::last_endpoint),result.get(zmq::sockopt::last_endpoint),state.get(zmq::sockopt::last_endpoint)});
      uint64_t version=0;
      while(!stop) {
        zmq::message_t msg; if(!control.recv(msg)) continue;
        wire::ControlCommand c; check(c.ParseFromArray(msg.data(),static_cast<int>(msg.size())),"wire command parse");
        wire::CommandResult r; r.set_protocol_version(1); r.set_request_id(c.request_id()); r.set_command(c.command());
        r.set_status(wire::RESULT_ACCEPTED); result.send(zmq::buffer(r.SerializeAsString()),zmq::send_flags::none);
        r.set_status(c.command()==wire::COMMAND_EXO_RAW ? wire::RESULT_FAILED : wire::RESULT_SUCCEEDED);
        if(r.status()==wire::RESULT_FAILED) { r.mutable_error()->set_code(wire::ERROR_INVALID_ARGUMENT); r.mutable_error()->set_message("raw disabled by mock"); }
        if(c.command()==wire::COMMAND_SET_EXO_MODE && c.set_exo_mode().mode()==wire::EXO_MODE_OFF) ++off;
        result.send(zmq::buffer(r.SerializeAsString()),zmq::send_flags::none);
        wire::StateSnapshot snapshot; snapshot.set_protocol_version(1); snapshot.set_state_version(++version);
        snapshot.set_timestamp_ns(987654321); snapshot.mutable_exo()->set_last_reply("mock firmware reply");
        state.send(zmq::buffer(snapshot.SerializeAsString()),zmq::send_flags::none);
      }
    } catch(...) {
      error=std::current_exception();
      try { ready.set_exception(error); } catch(...) {}
    }
  }
};
}
int main() {
  try {
    Peer peer; Service service; service.endpoints=peer.ready.get_future().get();
    grpc::ServerBuilder builder; int port=0;
    builder.AddListeningPort("127.0.0.1:0",grpc::InsecureServerCredentials(),&port);
    builder.RegisterService(&service); auto server=builder.BuildAndStart(); check(server && port>0,"mock RPC start");
    {
      Controller c(make_synapse_transport("127.0.0.1",port),Milliseconds(1500));
      c.connect(); c.set_mode(wire::EXO_MODE_CONNECTED);
      c.query("version"); c.poll(Milliseconds(100));
      check(c.state().has_value() && c.state()->timestamp_ns()==987654321,"real Tap state");
      bool rejected=false; try { c.raw("help"); } catch(const CommandError&) { rejected=true; }
      check(rejected,"real Tap rejection");
      c.disconnect(); check(peer.off==1,"real Tap OFF ack");
      c.connect(); c.disconnect(); check(peer.off==1,"transport-only connect does not own Exo");
    }
    exo_client* c=nullptr;
    check(exo_create("127.0.0.1",port,1500,&c)==0,"C create");
    check(exo_connect(c)==0,"C connect");
    check(exo_set_mode(c,EXO_CONNECTED)==0,"C mode");
    check(exo_raw(c,"help")==EXO_DEVICE_REJECTED,"C gate error");
    size_t required=0; exo_copy_result_json(c,nullptr,0,&required);
    std::vector<char> result(required); check(exo_copy_result_json(c,result.data(),result.size(),&required)==0,"C result copy");
    check(std::string(result.data()).find("raw disabled by mock")!=std::string::npos,"C error detail");
    check(exo_disconnect(c)==0,"C disconnect"); exo_destroy(c);
    check(peer.off==2,"C OFF ack");
    // Legacy discovery omits output direction. Exercise the full handshake,
    // state receive and C ABI, not only a matching enum predicate.
    service.output_type=synapse::TAP_TYPE_UNSPECIFIED;
    check(exo_create("127.0.0.1",port,1500,&c)==0,"legacy C create");
    check(exo_connect(c)==0,"legacy unspecified output handshake");
    check(exo_query(c,"version")==0,"legacy output query");
    check(exo_disconnect(c)==0,"legacy disconnect"); exo_destroy(c);
    {
      Controller legacy(make_synapse_transport("127.0.0.1",port),Milliseconds(1500));
      legacy.connect(); legacy.poll(Milliseconds(100));
      check(legacy.state().has_value(),"legacy output state receive");
      legacy.disconnect();
    }
    auto rejected_direction = [&] {
      auto transport=make_synapse_transport("127.0.0.1",port);
      try { transport->open(Milliseconds(1500)); }
      catch(const std::runtime_error& e) {
        return std::string(e.what()).find("wrong Tap direction:")!=std::string::npos;
      }
      return false;
    };
    service.output_type=synapse::TAP_TYPE_CONSUMER;
    check(rejected_direction(),"consumer output rejected");
    service.output_type=99;
    check(rejected_direction(),"unknown output direction rejected");
    service.output_type=synapse::TAP_TYPE_PRODUCER;
    service.control_type=synapse::TAP_TYPE_UNSPECIFIED;
    check(rejected_direction(),"unspecified control rejected");
    service.control_type=synapse::TAP_TYPE_PRODUCER;
    check(rejected_direction(),"producer control rejected");
    server->Shutdown();
    std::cout<<"Loopback gRPC/ZeroMQ/C ABI passed (localhost only)\n";
    return 0;
  } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
