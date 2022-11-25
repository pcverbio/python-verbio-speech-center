#!/bin/bash

workers="${WORKERS:-2}"

export OMP_NUM_THREADS=8
export OMP_WAIT_POLICY=PASSIVE
export KMP_AFFINITY=scatter

python3 server.py -j${workers} -m /asr4-$LANGUAGE.onnx -d /dict.ltr.txt -l $LANGUAGE -f /format-model.$LANGUAGE.fm
