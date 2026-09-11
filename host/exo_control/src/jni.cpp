#include "exo_control/exo.h"
#include <jni.h>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
exo_client* client(jlong handle) {
  if (!handle) throw std::invalid_argument("closed handle");
  return reinterpret_cast<exo_client*>(static_cast<intptr_t>(handle));
}
std::string bytes(JNIEnv* env, jbyteArray array) {
  if (!array) throw std::invalid_argument("null byte array");
  auto n = env->GetArrayLength(array);
  if (n > 4096) throw std::invalid_argument("text too long");
  std::string value(static_cast<size_t>(n), '\0');
  env->GetByteArrayRegion(array, 0, n, reinterpret_cast<jbyte*>(value.data()));
  if (value.find('\0') != std::string::npos) throw std::invalid_argument("embedded NUL");
  return value;
}
std::string copied(exo_client* c, int (*fn)(exo_client*, char*, size_t, size_t*)) {
  size_t required = 0;
  if (fn(c,nullptr,0,&required) != EXO_BUFFER_TOO_SMALL) throw std::runtime_error("native copy sizing failed");
  std::vector<char> buffer(required);
  if (fn(c,buffer.data(),buffer.size(),&required) != EXO_OK) throw std::runtime_error("native copy failed");
  return buffer.data();
}
// Standard UTF-8 conversion, not JNI's modified UTF-8 (firmware text may be Unicode).
jstring string(JNIEnv* env, const std::string& value) {
  auto data = env->NewByteArray(static_cast<jsize>(value.size()));
  if (!data) return nullptr;
  env->SetByteArrayRegion(data,0,static_cast<jsize>(value.size()),reinterpret_cast<const jbyte*>(value.data()));
  auto cls = env->FindClass("java/lang/String");
  if (!cls) { env->DeleteLocalRef(data); return nullptr; }
  auto ctor = env->GetMethodID(cls,"<init>","([BLjava/lang/String;)V");
  auto charset = env->NewStringUTF("UTF-8");
  auto result = ctor && charset ? static_cast<jstring>(env->NewObject(cls,ctor,data,charset)) : nullptr;
  env->DeleteLocalRef(data); env->DeleteLocalRef(cls); env->DeleteLocalRef(charset);
  return result;
}
void error(JNIEnv* env, int status, const std::string& message, const std::string& result) {
  if (env->ExceptionCheck()) return;
  auto cls = env->FindClass("org/sciencexyz/exo/ExoClient$NativeException");
  if (!cls) return;
  auto ctor = env->GetMethodID(cls,"<init>","(ILjava/lang/String;Ljava/lang/String;)V");
  auto msg = string(env,message), json = string(env,result);
  if (ctor && msg && json && !env->ExceptionCheck()) {
    auto exception = static_cast<jthrowable>(env->NewObject(cls,ctor,status,msg,json));
    if (exception) { env->Throw(exception); env->DeleteLocalRef(exception); }
  }
  env->DeleteLocalRef(msg); env->DeleteLocalRef(json); env->DeleteLocalRef(cls);
}
}
extern "C" JNIEXPORT jlong JNICALL Java_org_sciencexyz_exo_ExoClient_create(JNIEnv* env,jclass,jbyteArray host,jint port,jint timeout) {
  try {
    auto address = bytes(env,host);
    if (env->ExceptionCheck()) return 0;
    exo_client* c = nullptr;
    int rc = exo_create(address.c_str(),port,timeout,&c);
    if (rc) { error(env,rc,"client creation failed; check host/port/timeout",""); return 0; }
    return static_cast<jlong>(reinterpret_cast<intptr_t>(c));
  } catch (const std::exception& e) { error(env,EXO_FAILURE,e.what(),""); return 0; }
  catch (...) { error(env,EXO_FAILURE,"native failure",""); return 0; }
}
extern "C" JNIEXPORT jstring JNICALL Java_org_sciencexyz_exo_ExoClient_invoke(JNIEnv* env,jclass,jlong handle,jint op,jint arg,jbyteArray text,jintArray joints,jintArray values) {
  try {
    auto c = client(handle);
    int rc = EXO_INVALID_ARGUMENT;
    switch(op) {
      case 0: rc=exo_connect(c); break;
      case 1: rc=exo_disconnect(c); break;
      case 2: rc=exo_set_mode(c,arg); break;
      case 3: {
        if (!joints || !values) throw std::invalid_argument("null pose arrays");
        auto count=env->GetArrayLength(joints);
        if (count<1 || count>6 || count!=env->GetArrayLength(values)) throw std::invalid_argument("invalid pose arrays");
        jint j[6], v[6]; int ji[6], vi[6];
        env->GetIntArrayRegion(joints,0,count,j); env->GetIntArrayRegion(values,0,count,v);
        if (env->ExceptionCheck()) return nullptr;
        for(int i=0;i<count;++i) { ji[i]=j[i]; vi[i]=v[i]; }
        rc=exo_set_pose(c,ji,vi,static_cast<size_t>(count)); break;
      }
      case 4: case 5: {
        auto value=bytes(env,text); if (env->ExceptionCheck()) return nullptr;
        rc=op==4 ? exo_query(c,value.c_str()) : exo_raw(c,value.c_str()); break;
      }
      case 6: rc=exo_get_state(c); break;
      case 7: rc=exo_poll(c,arg); break;
      case 8: rc=EXO_OK; break;
      default: throw std::invalid_argument("unknown operation");
    }
    if (rc) { error(env,rc,copied(c,exo_copy_error),copied(c,exo_copy_result_json)); return nullptr; }
    return string(env,copied(c,op>=7 ? exo_copy_state_json : exo_copy_result_json));
  } catch (const std::exception& e) { error(env,EXO_FAILURE,e.what(),""); return nullptr; }
  catch (...) { error(env,EXO_FAILURE,"native failure",""); return nullptr; }
}
extern "C" JNIEXPORT void JNICALL Java_org_sciencexyz_exo_ExoClient_destroy(JNIEnv* env,jclass,jlong handle) {
  exo_client* c = nullptr;
  try {
    c=client(handle);
    int rc=exo_disconnect(c);
    auto message=copied(c,exo_copy_error), result=copied(c,exo_copy_result_json);
    exo_destroy(c); c=nullptr;
    if(rc) error(env,rc,message,result);
  } catch (const std::exception& e) { exo_destroy(c); error(env,EXO_FAILURE,e.what(),""); }
  catch (...) { exo_destroy(c); error(env,EXO_FAILURE,"native close failure",""); }
}
