# Experiment 03: targets included by the common native build.
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

set(QGRAPH_SYMPHONY_ROOT "${REPO_ROOT}/baselines/symphonyqg" CACHE PATH "Audited SymphonyQG source checkout")
add_executable(qgraph05_symphonyqg_disk_port symphonyqg_disk_port.cpp)
target_include_directories(
  qgraph05_symphonyqg_disk_port
  PRIVATE
    "${QGRAPH_SYMPHONY_ROOT}/symqglib"
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

add_executable(qgraph05_symphonyqg_search_test symphonyqg_search_test.cpp)
target_include_directories(qgraph05_symphonyqg_search_test PRIVATE "${QGRAPH_SYMPHONY_ROOT}/symqglib")
target_compile_options(qgraph05_symphonyqg_search_test PRIVATE -O3 -march=native -mavx512f -ffast-math -fopenmp -DEIGEN_DONT_PARALLELIZE)
target_link_libraries(qgraph05_symphonyqg_search_test PRIVATE qgraph05_direct_io OpenSSL::Crypto OpenMP::OpenMP_CXX pthread dl m)

add_executable(qgraph05_og_lvq_disk_port og_lvq_disk_port.cpp)
target_include_directories(qgraph05_og_lvq_disk_port PRIVATE
  "${REPO_ROOT}/baselines/svs/python/svs/include")
target_compile_definitions(qgraph05_og_lvq_disk_port PRIVATE FMT_HEADER_ONLY)
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


add_executable(qgraph05_og_lvq_buffer_test og_lvq_buffer_test.cpp)
target_include_directories(qgraph05_og_lvq_buffer_test PRIVATE "${REPO_ROOT}/baselines/svs/python/svs/include")
target_compile_definitions(qgraph05_og_lvq_buffer_test PRIVATE FMT_HEADER_ONLY)
add_test(NAME qgraph05_og_lvq_buffer_test COMMAND qgraph05_og_lvq_buffer_test)

add_executable(qgraph05_glass_official_search_test "${REPO_ROOT}/src/disk_bench/tests/audit_glass_official_search.cpp")
target_include_directories(qgraph05_glass_official_search_test PRIVATE
  "${REPO_ROOT}/baselines/pyglass" "${REPO_ROOT}/baselines/pyglass/third_party/helpa")
target_compile_options(qgraph05_glass_official_search_test PRIVATE -O2 -march=native)
target_link_libraries(qgraph05_glass_official_search_test PRIVATE
  qgraph05_direct_io OpenSSL::Crypto OpenMP::OpenMP_CXX pthread dl m)
add_test(NAME qgraph05_glass_official_search_test COMMAND qgraph05_glass_official_search_test)
add_test(NAME qgraph05_symphonyqg_search_test COMMAND qgraph05_symphonyqg_search_test)

# Unbudgeted official in-memory reference; never used for disk performance timing.
add_executable(qgraph05_symphonyqg_real_reference symphonyqg_real_reference.cpp)
target_include_directories(qgraph05_symphonyqg_real_reference PRIVATE "${QGRAPH_SYMPHONY_ROOT}/symqglib")
target_compile_options(qgraph05_symphonyqg_real_reference PRIVATE -O3 -march=native -mavx512f -ffast-math -fopenmp -DEIGEN_DONT_PARALLELIZE)
target_link_libraries(qgraph05_symphonyqg_real_reference PRIVATE qgraph05_direct_io OpenSSL::Crypto OpenMP::OpenMP_CXX pthread dl m)
add_executable(qgraph05_glass_real_reference glass_real_reference.cpp)
target_include_directories(qgraph05_glass_real_reference PRIVATE "${REPO_ROOT}/baselines/pyglass" "${REPO_ROOT}/baselines/pyglass/third_party/helpa")
target_compile_options(qgraph05_glass_real_reference PRIVATE -O3 -march=native -fopenmp)
target_link_libraries(qgraph05_glass_real_reference PRIVATE qgraph05_direct_io OpenSSL::Crypto OpenMP::OpenMP_CXX pthread dl m)
add_executable(qgraph05_og_lvq_distance_probe og_lvq_distance_probe.cpp)
target_include_directories(qgraph05_og_lvq_distance_probe PRIVATE "${REPO_ROOT}/baselines/svs/python/svs/include")
target_compile_definitions(qgraph05_og_lvq_distance_probe PRIVATE FMT_HEADER_ONLY)
target_compile_options(qgraph05_og_lvq_distance_probe PRIVATE -O3 -march=native -fopenmp)
target_link_libraries(qgraph05_og_lvq_distance_probe PRIVATE qgraph05_direct_io OpenSSL::Crypto OpenMP::OpenMP_CXX pthread dl m)
