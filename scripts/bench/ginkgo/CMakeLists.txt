# Small Ginkgo benchmark driver (BSD-3-Clause library, built by the owner, never bundled).
#   cmake -S . -B build -DCMAKE_PREFIX_PATH=<ginkgo install> && cmake --build build --config Release
cmake_minimum_required(VERSION 3.18)
project(ginkgo_bench LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Ginkgo 1.9 REQUIRED)
add_executable(ginkgo_bench ginkgo_bench.cpp)
target_link_libraries(ginkgo_bench Ginkgo::ginkgo)
