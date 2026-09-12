#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <deque>
#include <vector>
using std::min;
inline uint32_t mock_ms=1000;
inline uint32_t millis() {return mock_ms;}
struct MockRegisters {
  struct {struct {struct {struct {bool BK1RDY=false;} bit;} EPSTATUS;} DeviceEndpoint[7];} DEVICE;
};
inline MockRegisters regs;
inline MockRegisters* USB=&regs;
namespace arduino {struct USBSetup {};}
struct InterfaceDescriptor {int values[5];};
struct EndpointDescriptor {int values[4];};
#define D_INTERFACE(a,b,c,d,e) {{a,b,c,d,e}}
#define D_ENDPOINT(a,b,c,d) {{a,b,c,d}}
#define USB_ENDPOINT_TYPE_BULK 2
#define USB_ENDPOINT_OUT(x) (x)
#define USB_ENDPOINT_IN(x) ((x)|128)
#define EPX_SIZE 64
