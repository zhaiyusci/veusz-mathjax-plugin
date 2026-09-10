cmake_minimum_required(VERSION 3.16)

# mathjaxbridge: the JS host that the veusz MathJax plugin loads with ctypes.
#
# It evaluates a JavaScript bundle inside QuickJS and calls the global
# render()/renderInline() the bundle defines.  It is deliberately independent
# of veusz: no Python headers, no Qt, just QuickJS.
#
# QuickJS is a SEPARATE shared library (its own licence and its own release
# cycle), so this links against the IMPORT library of a shared build.
# QUICKJS_DLL is optional but recommended: it is copied next to the bridge,
# which is where the loader looks for the import (measured on Windows: the
# directory of the loading DLL is searched, the current directory is not).
#
#   cmake -S src -B build -DQUICKJS_SRC=/path/to/quickjs-ng \
#         -DQUICKJS_LIB=/path/to/quickjs-build-shared/qjs.lib \
#         -DQUICKJS_DLL=/path/to/quickjs-build-shared/qjs.dll
#   cmake --build build
#
# The built library is copied into ../data/ so the plugin finds it next to
# itself.

project(mathjaxbridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(QUICKJS_LIB "" CACHE FILEPATH "QuickJS import library (qjs.lib), from a -DBUILD_SHARED_LIBS=ON build")
set(QUICKJS_SRC "" CACHE PATH "Path to the QuickJS source tree (for quickjs.h)")
set(QUICKJS_DLL "" CACHE FILEPATH "Optional: the QuickJS shared library to copy into data/ (qjs.dll)")

if(NOT QUICKJS_LIB)
  message(FATAL_ERROR "QUICKJS_LIB is required, e.g. -DQUICKJS_LIB=/path/to/qjs.lib")
endif()
if(NOT QUICKJS_SRC)
  message(FATAL_ERROR "QUICKJS_SRC is required, e.g. -DQUICKJS_SRC=/path/to/quickjs-ng")
endif()

add_library(mathjaxbridge SHARED mathjax_bridge.cpp)
target_include_directories(mathjaxbridge PRIVATE "${QUICKJS_SRC}")
target_link_libraries(mathjaxbridge PRIVATE "${QUICKJS_LIB}")

# quickjs.h declares the API __declspec(dllimport) when this is set; upstream's
# CMake sets it automatically for consumers of the qjs target.
target_compile_definitions(mathjaxbridge PRIVATE USING_QJS_SHARED)

if(WIN32)
  target_link_libraries(mathjaxbridge PRIVATE kernel32 user32)
endif()

set_target_properties(mathjaxbridge PROPERTIES OUTPUT_NAME mathjaxbridge)

# ship it next to the plugin file
add_custom_command(TARGET mathjaxbridge POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_SOURCE_DIR}/../data"
  COMMAND ${CMAKE_COMMAND} -E copy "$<TARGET_FILE:mathjaxbridge>"
          "${CMAKE_CURRENT_SOURCE_DIR}/../data/"
  COMMENT "copying the bridge into data/")

if(QUICKJS_DLL)
  add_custom_command(TARGET mathjaxbridge POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy "${QUICKJS_DLL}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../data/"
    COMMENT "copying the QuickJS engine into data/")
endif()
