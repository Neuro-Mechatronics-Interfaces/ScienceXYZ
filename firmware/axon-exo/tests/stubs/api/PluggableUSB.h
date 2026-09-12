#pragma once
#include <Arduino.h>
namespace arduino {
class PluggableUSBModule {
 public:
  PluggableUSBModule(int,int,unsigned int*) {}
  virtual ~PluggableUSBModule()=default;
 protected:
  uint8_t pluggedInterface=0,pluggedEndpoint=1;
  virtual bool setup(USBSetup&)=0;
  virtual int getInterface(uint8_t*)=0;
  virtual int getDescriptor(USBSetup&)=0;
  virtual uint8_t getShortName(char*)=0;
};
}
struct MockPluggable {void plug(arduino::PluggableUSBModule*){}};
inline MockPluggable& PluggableUSB(){static MockPluggable p;return p;}
