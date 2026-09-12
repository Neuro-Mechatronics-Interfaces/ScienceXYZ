#include "AxonEnumProtocol.h"
#include <cstdlib>
#include <iostream>
#include <vector>
static void check(bool ok) { if (!ok) std::abort(); }
static std::vector<uint8_t> request(unsigned type = 3, unsigned len = 0) {
  std::vector<uint8_t> p(28 + len);
  p[1]=0xEE; p[2]=0xFF; p[3]=0xC0; p[8]=0x34; p[9]=0x12;
  p[20]=uint8_t(len); p[21]=uint8_t(len>>8); p[22]=uint8_t(type);
  p[24+len]=0x20;
  return p;
}
int main() {
  axon_exo::EnumProtocol parser;
  uint8_t reply[32]{};
  auto p=request();
  for (unsigned i=0;i<p.size();++i) check(parser.feed(p[i],reply)==(i==p.size()-1));
  check(reply[8]==0x34 && reply[9]==0x12 && reply[20]==4 && reply[22]==4);
  check(reply[24]==2 && reply[25]==0xF0 && reply[28]==0x20);
  // Coalesced requests and junk before a fragmented header.
  for (uint8_t b : {uint8_t(7),uint8_t(0),uint8_t(0)}) check(!parser.feed(b,reply));
  for (int repeat=0;repeat<3;++repeat)
    for (unsigned i=0;i<p.size();++i) check(parser.feed(p[i],reply)==(i==p.size()-1));
  // A command embedded in another message's payload must not be executed.
  auto other=request(10,64);
  std::copy(p.begin(),p.end(),other.begin()+24);
  for(auto b:other) check(!parser.feed(b,reply));
  auto bad=request(3,4);
  for(auto b:bad) check(!parser.feed(b,reply));
  // Reset after truncated transfer / USB disconnect, then rediscover.
  for(unsigned i=0;i<13;++i) check(!parser.feed(p[i],reply));
  parser.reset();
  for(unsigned i=0;i<p.size();++i) check(parser.feed(p[i],reply)==(i==p.size()-1));
  std::cout << "Axon enumeration parser tests passed\n";
}
