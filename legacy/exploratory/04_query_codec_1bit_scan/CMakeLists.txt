cmake_minimum_required(VERSION 3.20)

project(query_codec_1bit_scan LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(query_codec_1bit_scan query_codec_1bit_scan.cpp)
target_compile_options(
  query_codec_1bit_scan
  PRIVATE
    -O3
    -march=native
    -Wall
    -Wextra
    -Wpedantic
)

