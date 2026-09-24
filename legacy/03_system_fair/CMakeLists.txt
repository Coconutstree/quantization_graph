cmake_minimum_required(VERSION 3.20)

project(system_fair LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(QGRAPH_DEPS_ROOT "${REPO_ROOT}/baselines" CACHE PATH
    "Root containing faiss/upstream and builds/faiss-cmake43")
set(FAISS_ROOT "${QGRAPH_DEPS_ROOT}/faiss/upstream")
set(FAISS_BUILD "${QGRAPH_DEPS_ROOT}/builds/faiss-cmake43")
set(FAISS_LIB "${FAISS_BUILD}/faiss/libfaiss.a")

if(NOT EXISTS "${FAISS_LIB}")
  message(FATAL_ERROR "Faiss library not found: ${FAISS_LIB}")
endif()

find_package(OpenMP REQUIRED)
find_package(BLAS REQUIRED)

add_executable(faiss_hnsw_rerank_sweep faiss_hnsw_rerank_sweep.cpp)
target_include_directories(
  faiss_hnsw_rerank_sweep
  PRIVATE
    "${FAISS_ROOT}"
    "${FAISS_BUILD}"
)
target_compile_options(faiss_hnsw_rerank_sweep PRIVATE -O3 -march=native)
target_link_libraries(
  faiss_hnsw_rerank_sweep
  PRIVATE
    "${FAISS_LIB}"
    OpenMP::OpenMP_CXX
    ${BLAS_LIBRARIES}
    pthread
    dl
    m
)
