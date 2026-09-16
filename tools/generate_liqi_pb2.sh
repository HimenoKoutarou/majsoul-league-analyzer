#!/bin/sh
# 重新生成 liqi_combined_pb2.py（协议更新时执行）
cd "$(dirname "$0")/../app/services/majsoul"
python -m grpc_tools.protoc --python_out=. liqi_combined.proto