#include "usb_cdc_port.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
using namespace scifi2_hub::exo;
void check(bool ok) { if (!ok) { std::cerr << "USB assertion failed\n"; std::exit(1); } }
struct Fake : UsbBackend {
  std::vector<UsbInterface> topology{{0,0,2,2,{5,0x24,6,0,1},{}},
    {1,0,10,0,{},{{0x82,2,64},{3,2,64}}},
    {2,0,2,2,{5,0x24,6,2,3},{}}, {3,0,10,0,{},{{0x84,2,64},{5,2,64}}}};
  std::vector<int> claims, releases;
  int driver=0, claim_failure=-1, write_rc=0, read_rc=-7, writes=0, reads=0;
  std::string input="abcdef";
  bool open(const UsbCdcConfig&, std::vector<UsbInterface>& out, std::string&) override { out=topology; return true; }
  void close() override {}
  int kernel_active(int) override { return driver; }
  int claim(int i) override { if(i==claim_failure) return -6; claims.push_back(i); return 0; }
  void release(int i) override { releases.push_back(i); }
  int alternate(int i,int a) override { check(i==1 && a==0); return 0; }
  int control(int request,int value,int i,unsigned char* bytes,int size,unsigned timeout) override {
    check(i==0 && timeout>0 && timeout<=2000);
    if(request==0x20) { check(size==7 && bytes[0]==0x40 && bytes[1]==0x42 && bytes[2]==0x0f && bytes[6]==8); return 7; }
    check(request==0x22 && (value==0 || value==3)); return 0;
  }
  int bulk(int ep,unsigned char* bytes,int size,int& transferred,unsigned timeout) override {
    check(timeout>0 && timeout<=2000);
    if(ep==3) { ++writes; transferred=std::min(size,2); return write_rc; }
    check(ep==0x82 && size%64==0); ++reads;
    transferred=static_cast<int>(input.size()); std::copy(input.begin(),input.end(),bytes); input.clear(); return read_rc;
  }
  std::string describe(int e) const override { return std::to_string(e); }
};
int main() {
  auto fake=std::make_unique<Fake>(); auto* f=fake.get();
  auto port=make_usb_cdc_port({},std::move(fake));
  check(port->open()); check(f->claims==std::vector<int>({0,1}));
  check(port->write("hello")); check(f->writes==3);
  check(port->read(2,10)=="ab"); check(port->read(4,10)=="cdef"); check(f->reads==1);
  f->write_rc=-7; check(!port->write("motion")); check(f->writes==4); check(!port->is_open());
  check(f->releases==std::vector<int>({1,0}));
  check(port->open()); f->read_rc=-4; check(port->read(8,10).empty()); check(!port->is_open());
  fake=std::make_unique<Fake>(); f=fake.get(); f->claim_failure=1;
  port=make_usb_cdc_port({},std::move(fake)); check(!port->open()); check(f->releases==std::vector<int>({0}));
  fake=std::make_unique<Fake>(); f=fake.get(); f->driver=1;
  port=make_usb_cdc_port({},std::move(fake)); check(!port->open()); check(f->claims.empty());
  CdcEndpoints result; std::string error;
  auto topology=Fake().topology;
  check(select_cdc_endpoints(topology,0,result,error)); check(result.input==0x82 && result.output==3);
  topology[0].extra={0}; check(!select_cdc_endpoints(topology,0,result,error));
  topology=Fake().topology; topology.push_back(topology[1]);
  check(!select_cdc_endpoints(topology,0,result,error));
  topology=Fake().topology; topology[1].endpoints.pop_back();
  check(!select_cdc_endpoints(topology,0,result,error));
}
