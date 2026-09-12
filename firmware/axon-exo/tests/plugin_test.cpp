#include "scifi-peripheral-sdk/plugin.h"
#include <dlfcn.h>
#include <cstdlib>
#include <iostream>
#include <atomic>
#include <chrono>
#include <future>
#include <thread>
#include <cstring>
static void check(bool ok) { if (!ok) std::abort(); }
int main(int argc, char** argv) {
  check(argc==2);
  void* lib=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
  if(!lib) { std::cerr << dlerror() << '\n'; return 1; }
  auto entry=reinterpret_cast<const scifi::plugin::PluginDescriptor*(*)()>(dlsym(lib,"scifi_plugin_entry"));
  check(entry);
  const auto* d=entry();
  check(d->abi_version==3 && d->peripheral_id==0xF002 && d->make_record);
  check(!d->make_record(nullptr,nullptr));
  {
    zmq::context_t zmq;
    scifi::plugin::HostServices host{3,&zmq,"inproc://unused-tx","inproc://unused-rx"};
    scifi::plugin::PeripheralContext ctx{731,1};
    auto p=d->make_record(&ctx,&host);
    check(bool(p));
    const auto identity=p->to_proto();
    check(identity.name()=="NML Hand Exo" && identity.peripheral_id()==731);
    check(identity.type()==synapse::Peripheral::kBroadbandSource);
    check(p->validate_ephys_config({}).has_value());
    check(p->start_recording(20000,16,{},1,-1,-1)==scifi::Status::INVALID_PARAMETER);
    check(p->read_frames(1).empty());
    check(p->self_test({}).status().code()==synapse::StatusCode::kUnimplemented);
    std::vector<synapse::Channel> channels;
    for (unsigned motor : {11u,12u}) for (unsigned field=0;field<4;++field) {
      synapse::Channel c; c.set_id(channels.size()); c.set_electrode_id(4*motor+field);
      channels.push_back(c);
    }
    check(!p->validate_channels(channels));
    auto bad=channels; bad[4].set_electrode_id(44); check(bool(p->validate_channels(bad)));
    std::atomic<bool> done{false}; std::promise<void> bound;
    auto ready=bound.get_future();
    std::thread server([&]{
      zmq::socket_t input(zmq,zmq::socket_type::sub),output(zmq,zmq::socket_type::pub);
      input.set(zmq::sockopt::subscribe,""); input.set(zmq::sockopt::rcvtimeo,10);
      input.set(zmq::sockopt::linger,0); output.set(zmq::sockopt::linger,0);
      input.bind("inproc://unused-tx"); output.bind("inproc://unused-rx"); bound.set_value();
      while (!done) {
        zmq::message_t msg; if (!input.recv(msg)) continue;
        check(msg.size()==7*4);
        uint32_t request[7]; std::memcpy(request,msg.data(),sizeof(request));
        check(request[0]==1 && request[1]==0xF210 && request[2]==1 && request[4]==2);
        check(request[5]==11 && request[6]==12);
        // Two motors: valid -12.3 degrees sampled 7ms ago, and unavailable.
        uint32_t response[]={1,0xF211,77,1,request[3],5000,0,2,
                             uint32_t(uint16_t(-123))|(7u<<16),0,
                             uint32_t(uint16_t(INT16_MIN)),1u<<16};
        // A stale request token must not satisfy the pending poll.
        --response[4]; output.send(zmq::buffer(response),zmq::send_flags::none);
        ++response[4]; output.send(zmq::buffer(response),zmq::send_flags::none);
      }
    });
    ready.wait();
    check(p->start_recording(10,16,channels,1,-1,-1)==scifi::Status::OK);
    auto before=std::chrono::steady_clock::now();
    auto frames=p->read_frames(1);
    check(std::chrono::steady_clock::now()-before<std::chrono::milliseconds(100));
    check(frames.size()==1 && frames[0].frame_size()==8);
    check(frames[0].timestamp()==5000000000ULL && frames[0].sequence_number()==77);
    check(frames[0].sample_rate()==10 && frames[0].unix_timestamp_ns()>0);
    check(frames[0][0]==-123 && frames[0][1]==7 && frames[0][4]==INT16_MIN && frames[0][7]==1);
    check(p->stop_recording()==scifi::Status::OK);
    check(p->read_frames(1).empty());
    // Restart exercises socket lifetime and fresh handshake.
    check(p->start_recording(10,16,channels,1,-1,-1)==scifi::Status::OK);
    p.reset(); // concrete factory deleter must stop/join the worker
    done=true; server.join();
    p=d->make_record(&ctx,&host);
    check(p->start_recording(10,16,channels,1,-1,-1)==scifi::Status::CONNECTION_FAILED);
    check(p->read_frames(1).empty());
  }
  dlclose(lib);
  std::cout << "Plugin ABI, polling, source time, missing motor, restart and teardown tests passed\n";
}
