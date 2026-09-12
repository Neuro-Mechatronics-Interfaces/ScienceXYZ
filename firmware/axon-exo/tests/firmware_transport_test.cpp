#include <cstdlib>
#include <iostream>
#include "AxonUsbPeripheral.h"
void check(bool v){if(!v)std::abort();}
uint32_t word(const std::vector<uint8_t>& p,unsigned offset) {
  return uint32_t(p[offset])|uint32_t(p[offset+1])<<8|uint32_t(p[offset+2])<<16|uint32_t(p[offset+3])<<24;
}
void append(std::vector<uint8_t>& p,uint32_t w){for(int i=0;i<4;++i)p.push_back(w>>(8*i));}
std::vector<uint8_t> packet(unsigned type,std::vector<uint32_t> payload) {
  std::vector<uint8_t> bytes;
  for(uint32_t w:{0xC0FFEE00u,0u,0x300u,0u,0u,uint32_t(payload.size()*4)|(type<<16)})append(bytes,w);
  for(auto w:payload) append(bytes,w);
  append(bytes,0x20); return bytes;
}
unsigned reads=0;
bool read_angle(void*,uint8_t id,int16_t& value){++reads;value=-123;return id!=12;}
void tick(axon_exo::AxonUsbPeripheral& p) {
  ++mock_ms; USB->DEVICE.DeviceEndpoint[2].EPSTATUS.bit.BK1RDY=false;
  const auto before=reads;p.poll(read_angle,nullptr);check(reads-before<=1);
}
int main() {
  axon_exo::AxonUsbPeripheral p;
  auto request=packet(0xF210,{1,42,2,11,12});
  // Byte-fragmented request, then bounded per-motor sampling and chunked TX.
  for(auto b:request){USBDevice.input.push_back(b);tick(p);}
  for(int i=0;i<10;++i)tick(p);
  check(reads==2 && USBDevice.output.size()==64);
  check(word(USBDevice.output,4)==0x300 && word(USBDevice.output,20)==(0xF2110000u|36));
  check(word(USBDevice.output,28)==42 && word(USBDevice.output,40)==2);
  check(int16_t(word(USBDevice.output,44))==-123);
  check(int16_t(word(USBDevice.output,52))==INT16_MIN && word(USBDevice.output,56)>>16==1);
  USBDevice.output.clear();
  // Unknown packet payload may contain magic/enumeration: never execute it.
  const auto embedded=packet(3,{});std::vector<uint32_t> words;
  for(unsigned i=0;i<embedded.size();i+=4)words.push_back(word(embedded,i));
  auto unknown=packet(99,words);
  USBDevice.input.insert(USBDevice.input.end(),unknown.begin(),unknown.end());
  for(int i=0;i<10;++i) tick(p);
  check(USBDevice.output.empty());
  USBDevice.input.insert(USBDevice.input.end(),embedded.begin(),embedded.end());
  for(int i=0;i<10;++i)tick(p);
  check(USBDevice.output.size()==32 && word(USBDevice.output,24)==0xF002);
  USBDevice.output.clear();
  // Duplicate motor IDs cannot start bus reads.
  auto duplicate=packet(0xF210,{1,43,2,11,11});
  USBDevice.input.insert(USBDevice.input.end(),duplicate.begin(),duplicate.end());
  for(int i=0;i<10;++i) tick(p);
  check(reads==2 && USBDevice.output.empty());
  std::vector<uint32_t> full{1,44,18};
  for(unsigned id=1;id<=18;++id)full.push_back(id);
  auto largest=packet(0xF210,full);
  USBDevice.input.insert(USBDevice.input.end(),largest.begin(),largest.end());
  for(int i=0;i<40;++i) tick(p);
  check(reads==20 && USBDevice.output.size()==192);
  check(word(USBDevice.output,40)==18);
  std::cout<<"Firmware fragmented framing, measured/missing angles, bounded polling and discovery passed\n";
}
