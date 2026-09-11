#include <exo_control/controller.hpp>
int main() {
  exo_control::Controller c(exo_control::make_synapse_transport("127.0.0.1"));
  return c.connected() ? 1 : 0;
}
