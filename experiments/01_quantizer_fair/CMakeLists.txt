cmake_minimum_required(VERSION 3.20)

project(quantizer_fair LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(FAISS_ROOT "${REPO_ROOT}/baselines/faiss/upstream")
set(FAISS_BUILD "${REPO_ROOT}/baselines/builds/faiss-cmake43")
set(FAISS_LIB "${FAISS_BUILD}/faiss/libfaiss.a")

if(NOT EXISTS "${FAISS_LIB}")
  message(FATAL_ERROR "Faiss library not found: ${FAISS_LIB}")
endif()

find_package(OpenMP REQUIRED)
find_package(BLAS REQUIRED)

add_executable(faiss_quantizer_smoke faiss_quantizer_smoke.cpp)
add_executable(faiss_hard_negative_candidates faiss_hard_negative_candidates.cpp)
target_include_directories(
  faiss_quantizer_smoke
  PRIVATE
    "${FAISS_ROOT}"
    "${FAISS_BUILD}"
    "${REPO_ROOT}/Ours/core"
)
target_compile_options(faiss_quantizer_smoke PRIVATE -O3 -march=native)
target_include_directories(
  faiss_hard_negative_candidates
  PRIVATE
    "${FAISS_ROOT}"
    "${FAISS_BUILD}"
)
target_compile_options(faiss_hard_negative_candidates PRIVATE -O3 -march=native)
target_link_libraries(
  faiss_quantizer_smoke
  PRIVATE
    "${FAISS_LIB}"
    OpenMP::OpenMP_CXX
    ${BLAS_LIBRARIES}
    pthread
    dl
    m
)
target_link_libraries(
  faiss_hard_negative_candidates
  PRIVATE
    "${FAISS_LIB}"
    OpenMP::OpenMP_CXX
    ${BLAS_LIBRARIES}
    pthread
    dl
    m
)
