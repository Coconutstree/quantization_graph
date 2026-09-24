# Experiment 01: targets included by the common native build.
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


add_test(
  NAME qgraph05_quantizer_port_integration
  COMMAND
    "${Python3_EXECUTABLE}"
    "${REPO_ROOT}/src/disk_bench/tests/test_quantizer_native_port.py"
    "$<TARGET_FILE:qgraph05_quantizer_port>"
)
