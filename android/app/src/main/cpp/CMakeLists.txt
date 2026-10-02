cmake_minimum_required(VERSION 3.10.2)
project(mahjong_native)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(mahjong_native SHARED
    native_bridge.cpp
    sichuan_solver.cpp
    tile_memory.cpp
    game_fsm.cpp
)

target_link_libraries(mahjong_native
    android
    log
)
