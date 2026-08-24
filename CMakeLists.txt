cmake_minimum_required(VERSION 3.20)

project(quantization_graph_repro LANGUAGES CXX)

find_program(BASH_PROGRAM bash REQUIRED)
find_program(GXX_PROGRAM g++ REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

option(QGRAPH_SETUP_CPP_DEPS "Prepare repository-local C/C++ dependencies before building" ON)
option(QGRAPH_SETUP_BASELINES "Clone/build third-party baselines before formal build" ON)
option(QGRAPH_BUILD_FORMAL "Build formal local native/Rust ports" ON)
option(QGRAPH_WRITE_05_PORTS "Regenerate experiments/05_disk_system_fair/ports.local.json" ON)

set(QGRAPH_JOBS "${CMAKE_BUILD_PARALLEL_LEVEL}" CACHE STRING "Parallel jobs passed to repository build scripts; empty lets scripts use nproc")
set(QGRAPH_CARGO_OFFLINE "0" CACHE STRING "Set to 1 to force cargo --offline in scripts/build_formal_local.sh")
set(QGRAPH_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")

function(qgraph_env out_var)
  set(env_values
    "PATH=$ENV{HOME}/.local/bin:$ENV{HOME}/.cargo/bin:$ENV{PATH}"
    "CMAKE_BIN=${CMAKE_COMMAND}"
    "CMAKE_GENERATOR=Ninja"
    "FORMAL_CXX_BIN=${GXX_PROGRAM}"
    "CXX_BIN=${GXX_PROGRAM}"
    "CARGO_OFFLINE=${QGRAPH_CARGO_OFFLINE}"
  )
  if(QGRAPH_JOBS)
    list(APPEND env_values "JOBS=${QGRAPH_JOBS}")
  endif()
  set(${out_var} ${env_values} PARENT_SCOPE)
endfunction()

qgraph_env(QGRAPH_SCRIPT_ENV)

if(QGRAPH_SETUP_CPP_DEPS)
  add_custom_target(setup_cpp_deps
    COMMAND ${CMAKE_COMMAND} -E env ${QGRAPH_SCRIPT_ENV}
            ${BASH_PROGRAM} scripts/setup_cpp_deps_local.sh
    WORKING_DIRECTORY ${QGRAPH_ROOT}
    USES_TERMINAL
    COMMENT "Preparing repository-local C/C++ dependencies"
  )
else()
  add_custom_target(setup_cpp_deps
    COMMAND ${CMAKE_COMMAND} -E echo "Skipping setup_cpp_deps (QGRAPH_SETUP_CPP_DEPS=OFF)"
  )
endif()

if(QGRAPH_SETUP_BASELINES)
  add_custom_target(setup_deps
    COMMAND ${CMAKE_COMMAND} -E env ${QGRAPH_SCRIPT_ENV} SKIP_CPP_DEPS=1
            ${BASH_PROGRAM} scripts/setup_deps.sh
    WORKING_DIRECTORY ${QGRAPH_ROOT}
    DEPENDS setup_cpp_deps
    USES_TERMINAL
    COMMENT "Preparing and building third-party baselines"
  )
else()
  add_custom_target(setup_deps
    COMMAND ${CMAKE_COMMAND} -E echo "Skipping setup_deps (QGRAPH_SETUP_BASELINES=OFF)"
    DEPENDS setup_cpp_deps
  )
endif()

if(QGRAPH_BUILD_FORMAL)
  add_custom_target(formal_local
    COMMAND ${CMAKE_COMMAND} -E env ${QGRAPH_SCRIPT_ENV}
            ${BASH_PROGRAM} scripts/build_formal_local.sh
    WORKING_DIRECTORY ${QGRAPH_ROOT}
    DEPENDS setup_deps
    USES_TERMINAL
    COMMENT "Building formal local experiment binaries"
  )
else()
  add_custom_target(formal_local
    COMMAND ${CMAKE_COMMAND} -E echo "Skipping formal_local (QGRAPH_BUILD_FORMAL=OFF)"
    DEPENDS setup_deps
  )
endif()

if(QGRAPH_WRITE_05_PORTS)
  add_custom_target(write_05_ports
    COMMAND ${CMAKE_COMMAND} -E env ${QGRAPH_SCRIPT_ENV}
            ${Python3_EXECUTABLE} scripts/write_05_ports_local.py
    WORKING_DIRECTORY ${QGRAPH_ROOT}
    DEPENDS formal_local
    USES_TERMINAL
    COMMENT "Regenerating 05 native port registry"
  )
else()
  add_custom_target(write_05_ports
    COMMAND ${CMAKE_COMMAND} -E echo "Skipping write_05_ports (QGRAPH_WRITE_05_PORTS=OFF)"
    DEPENDS formal_local
  )
endif()

add_custom_target(repro ALL
  DEPENDS write_05_ports
)

add_custom_target(download_data
  COMMAND ${CMAKE_COMMAND} -E env ${QGRAPH_SCRIPT_ENV}
          ${BASH_PROGRAM} scripts/download_data.sh
  WORKING_DIRECTORY ${QGRAPH_ROOT}
  USES_TERMINAL
  COMMENT "Downloading and converting public datasets"
)

add_custom_target(check_datasets
  COMMAND ${Python3_EXECUTABLE} scripts/check_datasets.py
          --datasets dbpedia gist agnews --data-root data --out-root results
  WORKING_DIRECTORY ${QGRAPH_ROOT}
  USES_TERMINAL
  COMMENT "Checking prepared datasets"
)
