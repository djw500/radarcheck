#!/bin/bash
# Set up environment for building radarcheck-worker in the sandbox
export PATH="/home/dev/.local/bin:$PATH"
export CMAKE_GENERATOR=Ninja
export LIBCLANG_PATH="/home/dev/.local/lib/python3.11/site-packages/clang/native"
export BINDGEN_EXTRA_CLANG_ARGS="-I/usr/lib/gcc/aarch64-linux-gnu/14/include"
source /home/dev/.cargo/env
