#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  REVIEW_PYTHON=.venv/bin/python
else
  REVIEW_PYTHON=python3
fi
"$REVIEW_PYTHON" review.py doctor --workspace workspace &&
"$REVIEW_PYTHON" review.py open --workspace workspace --port 0
if [ "$?" -ne 0 ]; then
  echo '请按“使用说明.txt”配置Python及材料依赖。'
  read -r REVIEW_WAIT
fi
