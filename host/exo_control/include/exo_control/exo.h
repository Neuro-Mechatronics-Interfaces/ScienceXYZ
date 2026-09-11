#ifndef SCIENCEXYZ_EXO_H
#define SCIENCEXYZ_EXO_H
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32) && !defined(EXO_STATIC)
# ifdef EXO_BUILD
#  define EXO_API __declspec(dllexport)
# else
#  define EXO_API __declspec(dllimport)
# endif
#elif defined(__GNUC__)
# define EXO_API __attribute__((visibility("default")))
#else
# define EXO_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct exo_client exo_client;
enum exo_status { EXO_OK=0, EXO_INVALID_ARGUMENT=1, EXO_FAILURE=2, EXO_DEVICE_REJECTED=3, EXO_BUFFER_TOO_SMALL=4, EXO_TIMEOUT=5 };
enum exo_mode { EXO_OFF=1, EXO_EXTERNAL=2, EXO_DECODE=3, EXO_CONNECTED=4 };
enum exo_joint { EXO_THUMB=1, EXO_INDEX=2, EXO_MIDDLE=3, EXO_RING=4, EXO_PINKY=5, EXO_WRIST=6 };
// ABI version; all calls synchronous, serialized on one owner thread. No device
// access during create. Strings are UTF-8. Pointer lifetime ends at destroy.
EXO_API uint32_t exo_abi_version(void);
EXO_API int exo_create(const char* host, int rpc_port, int timeout_ms, exo_client** out);
EXO_API int exo_connect(exo_client*);
EXO_API int exo_disconnect(exo_client*); // OFF attempted before sockets close
EXO_API void exo_destroy(exo_client*); // fallback teardown; call disconnect to observe errors
EXO_API int exo_set_mode(exo_client*, int mode);
EXO_API int exo_set_pose(exo_client*, const int* joints, const int* values, size_t count);
EXO_API int exo_query(exo_client*, const char* query);
EXO_API int exo_raw(exo_client*, const char* command);
EXO_API int exo_get_state(exo_client*);
EXO_API int exo_poll(exo_client*, int timeout_ms);
// Copies include NUL. required reports bytes INCLUDING NUL; null buffer/0 is a
// sizing query. Copy functions never issue device commands or replace errors.
EXO_API int exo_copy_error(exo_client*, char* buffer, size_t capacity, size_t* required);
EXO_API int exo_copy_result_json(exo_client*, char* buffer, size_t capacity, size_t* required);
EXO_API int exo_copy_state_json(exo_client*, char* buffer, size_t capacity, size_t* required);
#ifdef __cplusplus
}
#endif
#endif
