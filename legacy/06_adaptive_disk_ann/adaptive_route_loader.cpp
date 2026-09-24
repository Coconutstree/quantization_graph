#include "adaptive_route_loader.hpp"

#include <cstring>
#include <fstream>
#include <stdexcept>

namespace adaptive_route {
namespace {

std::vector<uint8_t> read_bytes(const std::string &path, size_t expected) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    in.seekg(0, std::ios::end);
    const auto n = static_cast<size_t>(in.tellg());
    in.seekg(0);
    if (n != expected) throw std::runtime_error("size mismatch: " + path);
    std::vector<uint8_t> out(n);
    in.read(reinterpret_cast<char *>(out.data()), static_cast<std::streamsize>(n));
    if (!in) throw std::runtime_error("short read: " + path);
    return out;
}

std::vector<float> read_f32_npy(const std::string &path, size_t count) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    char magic[6]{};
    in.read(magic, 6);
    if (std::memcmp(magic, "\x93NUMPY", 6) != 0) throw std::runtime_error("invalid npy: " + path);
    uint8_t major = 0, minor = 0;
    in.read(reinterpret_cast<char *>(&major), 1);
    in.read(reinterpret_cast<char *>(&minor), 1);
    uint32_t header_len = 0;
    if (major == 1) {
        uint16_t n = 0; in.read(reinterpret_cast<char *>(&n), 2); header_len = n;
    } else if (major == 2 || major == 3) {
        in.read(reinterpret_cast<char *>(&header_len), 4);
    } else throw std::runtime_error("unsupported npy version");
    std::string header(header_len, '\0');
    in.read(header.data(), static_cast<std::streamsize>(header.size()));
    if (header.find("'descr': '<f4'") == std::string::npos &&
        header.find("\"descr\": \"<f4\"") == std::string::npos)
        throw std::runtime_error("npy is not little-endian float32: " + path);
    std::vector<float> out(count);
    in.read(reinterpret_cast<char *>(out.data()), static_cast<std::streamsize>(count * sizeof(float)));
    if (!in) throw std::runtime_error("short npy payload: " + path);
    return out;
}

float half_to_float(uint16_t h) {
    const uint32_t sign = (h & 0x8000u) << 16;
    const uint32_t exp = (h >> 10) & 0x1fu;
    const uint32_t frac = h & 0x3ffu;
    uint32_t bits;
    if (exp == 0) bits = sign | (frac ? 0x3f000000u : 0u);
    else if (exp == 31) bits = sign | 0x7f800000u | (frac << 13);
    else bits = sign | ((exp + 112u) << 23) | (frac << 13);
    float value; std::memcpy(&value, &bits, sizeof(value)); return value;
}

}  // namespace

RouteArtifacts load_artifacts(const std::string &codes_path,
                              const std::string &scales_path,
                              const std::string &mean_npy_path,
                              const std::string &components_npy_path,
                              size_t rows, size_t route_dim, size_t input_dim) {
    if (!rows || !route_dim || !input_dim) throw std::invalid_argument("invalid artifact shape");
    RouteArtifacts out;
    out.rows = rows; out.route_dim = route_dim; out.input_dim = input_dim;
    const size_t bytes = (route_dim + 7U) / 8U;
    out.codes = read_bytes(codes_path, rows * bytes);
    const auto scale_bytes = read_bytes(scales_path, rows * sizeof(uint16_t));
    out.scales.resize(rows);
    for (size_t i = 0; i < rows; ++i) {
        uint16_t h; std::memcpy(&h, scale_bytes.data() + i * 2, 2);
        out.scales[i] = half_to_float(h);
    }
    out.mean = read_f32_npy(mean_npy_path, input_dim);
    out.components = read_f32_npy(components_npy_path, route_dim * input_dim);
    return out;
}

}  // namespace adaptive_route
