cmake_minimum_required(VERSION 3.20)

project(qgraph05_native_io LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_library(LIBAIO_LIBRARY aio REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(OpenMP REQUIRED)
find_package(BLAS REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../..")
set(FAISS_ROOT "${REPO_ROOT}/baselines/faiss/upstream")
set(FAISS_BUILD "${REPO_ROOT}/baselines/builds/faiss-cmake43")
set(FAISS_LIB "${FAISS_BUILD}/faiss/libfaiss.a")
set(SAQ_ROOT "${REPO_ROOT}/baselines/saq")
set(LOCAL_DEPS "${REPO_ROOT}/baselines/deps/local")
set(glog_DIR "${LOCAL_DEPS}/lib/cmake/glog")
set(fmt_DIR "${LOCAL_DEPS}/lib/cmake/fmt")
find_package(glog CONFIG REQUIRED)
find_package(fmt CONFIG REQUIRED)
find_package(GFlags REQUIRED)

add_library(qgraph05_direct_io STATIC direct_io.cpp)
target_include_directories(qgraph05_direct_io PUBLIC "${CMAKE_CURRENT_LIST_DIR}")
target_compile_options(qgraph05_direct_io PRIVATE -O3 -Wall -Wextra -Wpedantic)
target_link_libraries(qgraph05_direct_io PUBLIC "${LIBAIO_LIBRARY}")

add_executable(qgraph05_direct_io_test direct_io_test.cpp)
target_compile_options(qgraph05_direct_io_test PRIVATE -O2 -Wall -Wextra -Wpedantic)
target_link_libraries(qgraph05_direct_io_test PRIVATE qgraph05_direct_io)

add_executable(qgraph05_quantizer_port quantizer_port.cpp)
target_include_directories(
  qgraph05_quantizer_port
  PRIVATE
    "${FAISS_ROOT}"
    "${FAISS_BUILD}"
    "${REPO_ROOT}/Ours/core"
)
target_compile_options(qgraph05_quantizer_port PRIVATE -O3 -march=native -Wall -Wextra)
target_link_libraries(
  qgraph05_quantizer_port
  PRIVATE
    qgraph05_direct_io
    "${FAISS_LIB}"
    OpenSSL::Crypto
    OpenMP::OpenMP_CXX
    ${BLAS_LIBRARIES}
    pthread
    dl
    m
)

add_executable(qgraph05_saq_quantizer_port saq_quantizer_port.cpp)
target_include_directories(
  qgraph05_saq_quantizer_port
  PRIVATE
    "${FAISS_ROOT}"
    "${FAISS_BUILD}"
    "${REPO_ROOT}/Ours/core"
    "${SAQ_ROOT}/saqlib"
    "${LOCAL_DEPS}/include"
)
target_compile_options(
  qgraph05_saq_quantizer_port
  PRIVATE
    -O3 -march=native -mavx512f -mavx512dq -mavx512bw -mavx512vl
    -ffp-contract=off -fno-finite-math-only -Wall -Wextra
)
set_target_properties(
  qgraph05_saq_quantizer_port
  PROPERTIES BUILD_RPATH "${LOCAL_DEPS}/lib" INSTALL_RPATH "${LOCAL_DEPS}/lib"
)
target_link_libraries(
  qgraph05_saq_quantizer_port
  PRIVATE
    qgraph05_direct_io
    "${FAISS_LIB}"
    glog::glog
    fmt::fmt
    ${GFLAGS_LIBRARIES}
    OpenSSL::Crypto
    OpenMP::OpenMP_CXX
    ${BLAS_LIBRARIES}
    pthread
    dl
    m
)

add_executable(qgraph05_glass_disk_port glass_disk_port.cpp)
target_include_directories(
  qgraph05_glass_disk_port
  PRIVATE
    "${REPO_ROOT}/baselines/pyglass"
    "${REPO_ROOT}/baselines/pyglass/third_party/helpa"
)
target_compile_options(
  qgraph05_glass_disk_port
  PRIVATE
    -O3 -march=native -fopenmp -Wall -Wextra
)
target_link_libraries(
  qgraph05_glass_disk_port
  PRIVATE
    qgraph05_direct_io
    OpenSSL::Crypto
    OpenMP::OpenMP_CXX
    pthread
    dl
    m
)

add_executable(qgraph05_symphonyqg_disk_port symphonyqg_disk_port.cpp)
target_include_directories(
  qgraph05_symphonyqg_disk_port
  PRIVATE
    "${REPO_ROOT}/baselines/symphonyqg/symqglib"
)
target_compile_options(
  qgraph05_symphonyqg_disk_port
  PRIVATE
    -O3 -march=native -mavx512f -ffast-math -fopenmp -Wall -Wextra
    -DEIGEN_DONT_PARALLELIZE
)
target_link_libraries(
  qgraph05_symphonyqg_disk_port
  PRIVATE
    qgraph05_direct_io
    OpenSSL::Crypto
    OpenMP::OpenMP_CXX
    pthread
    dl
    m
)

add_executable(qgraph05_og_lvq_disk_port og_lvq_disk_port.cpp)
target_compile_options(
  qgraph05_og_lvq_disk_port
  PRIVATE
    -O3 -march=native -fopenmp -Wall -Wextra
)
set_target_properties(
  qgraph05_og_lvq_disk_port
  PROPERTIES
    BUILD_RPATH "${REPO_ROOT}/baselines/svs/python/svs/lib64"
    INSTALL_RPATH "${REPO_ROOT}/baselines/svs/python/svs/lib64"
)
target_link_libraries(
  qgraph05_og_lvq_disk_port
  PRIVATE
    qgraph05_direct_io
    OpenSSL::Crypto
    OpenMP::OpenMP_CXX
    pthread
    dl
    m
)

enable_testing()
add_test(NAME qgraph05_direct_io_test COMMAND qgraph05_direct_io_test)
add_test(
  NAME qgraph05_quantizer_port_integration
  COMMAND
    "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_LIST_DIR}/../tests/test_quantizer_native_port.py"
    "$<TARGET_FILE:qgraph05_quantizer_port>"
)
