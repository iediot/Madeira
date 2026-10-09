// Exercise the vendored MSL compiler in MoltenVK's namespace.
#include "spirv_msl.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <cstring>

int main(int argc, char** argv) {
    if (argc != 3) { return 2; }
    std::ifstream file(argv[1], std::ios::binary);
    std::vector<char> bytes((std::istreambuf_iterator<char>(file)), {});
    if (bytes.empty() || bytes.size() % 4) { return 2; }
    std::vector<uint32_t> spirv(bytes.size() / 4);
    std::memcpy(spirv.data(), bytes.data(), bytes.size());
    using namespace MVK_spirv_cross;
    CompilerMSL compiler(spirv);
    auto options = compiler.get_msl_options();
    options.platform = CompilerMSL::Options::iOS;
    options.pad_fragment_output_components = true;
    compiler.set_msl_options(options);
    auto type = std::string(argv[2]);
    if (type != "none") {
        compiler.set_fragment_output_type(0, type == "uint" ? SPIRType::UInt :
                                            type == "int" ? SPIRType::Int : SPIRType::Float);
    }
    std::cout << compiler.compile();
}
