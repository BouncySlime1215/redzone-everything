#!/bin/sh
# Build rzseek.dex from RzSeek.java. Needs a JDK plus Android's d8 (any SDK build-tools) or the dx jar.
#   D8=~/Library/Android/sdk/build-tools/34.0.0/d8 sh tools/rzseek/build.sh
set -e
cd "$(dirname "$0")"
rm -rf build && mkdir -p build/classes
javac --release 8 -d build/classes RzSeek.java
if [ -n "$D8" ]; then
  "$D8" --min-api 26 --output build build/classes/RzSeek.class && mv build/classes.dex rzseek.dex
elif [ -n "$DX_JAR" ]; then
  java -jar "$DX_JAR" --dex --output=rzseek.dex build/classes
else
  echo "set D8=<path to build-tools d8> or DX_JAR=<dalvik-dx jar>" >&2; exit 1
fi
rm -rf build
ls -l rzseek.dex
