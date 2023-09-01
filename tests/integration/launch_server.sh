#!/bin/bash

set -eo pipefail

if [ $# -lt 0 ]
then
      echo "Usage: launch_server.sh <config_path> [<with_gpu>]"
      exit -1
fi

CONFIG=$1
export CUDA_VISIBLE_DEVICES=1

if [ $# -gt 1 ]
then
      export W2V_GPU=${USE_GPU:="True"}
fi

python3 bin/server.py -C ${CONFIG} -s1 -L1 -w2 -v TRACE &



export TIME=30
echo "Server launched, sleeping by ${TIME}"
sleep ${TIME}
