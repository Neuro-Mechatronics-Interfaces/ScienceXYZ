#include "task_state.hpp"
#include <google/protobuf/struct.pb.h>
#include <google/protobuf/util/json_util.h>
#include <fstream>
#include <iostream>
#include <sstream>
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::ifstream file(argv[1]);
  std::ostringstream text; text << file.rdbuf();
  google::protobuf::Value value;
  if (!google::protobuf::util::JsonStringToMessage(text.str(), &value).ok()) return 2;
  const auto& fields = value.struct_value().fields();
  if (!fields.contains("definition") || !fields.contains("definition_hash")) return 2;
  auto parsed = scifi2_hub::task::parse_task_definition(fields.at("definition"), 5);
  if (!parsed) { std::cerr << parsed.result.field << ": " << parsed.result.message << '\n'; return 1; }
  std::cout << parsed.definition.definition_hash << '\n';
  return parsed.definition.definition_hash == fields.at("definition_hash").string_value() ? 0 : 1;
}
