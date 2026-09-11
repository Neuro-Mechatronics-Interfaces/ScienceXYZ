#include <exo_control/exo.h>
// Construction/destruction only: never connects to any network or hardware.
int main(void) {
  exo_client* client = 0;
  if (exo_abi_version() != 1) return 1;
  if (exo_create("127.0.0.1", 647, 20, &client) != EXO_OK) return 2;
  if (exo_disconnect(client) != EXO_OK) return 3;
  exo_destroy(client);
  return 0;
}
