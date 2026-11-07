#!/usr/bin/env bash
# configure-vulkan.sh — конфигурация Vulkan-профиля llama.cpp (запускать руками/скриптом)
set -e
SRC=/home/rover/build-llama/llama.cpp
BUILD=/home/rover/build-llama/build-vulkan
cmake -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON \
  -DGGML_NATIVE=OFF \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_SERVER=ON \
  -DGGML_RPC=OFF \
  -DCMAKE_PREFIX_PATH="/home/rover/build-llama/vk-install;/home/rover/build-llama/spirv-install" \
  -DCMAKE_CXX_FLAGS="-I/home/rover/build-llama/spirv-install/include"
echo "=== CONFIGURE DONE ==="
cmake --build "$BUILD" -j 6 2>&1 | tail -5
echo "=== BUILD DONE ==="
