#pragma once
#include <Arduino.h>
struct MockDevice {
  bool configured_=true;
  std::deque<uint8_t> input;
  std::vector<uint8_t> output;
  bool configured() {return configured_;}
  uint32_t available(unsigned) {return input.size();}
  int recv(unsigned) {if(input.empty())return -1; int v=input.front();input.pop_front();return v;}
  uint32_t send(unsigned ep,const void* p,uint32_t n) {
    if (USB->DEVICE.DeviceEndpoint[ep].EPSTATUS.bit.BK1RDY || n>64) std::abort();
    const auto* b=static_cast<const uint8_t*>(p); output.insert(output.end(),b,b+n);
    USB->DEVICE.DeviceEndpoint[ep].EPSTATUS.bit.BK1RDY=true; return n;
  }
  int sendControl(const void*,unsigned n) {return n;}
};
inline MockDevice USBDevice;
