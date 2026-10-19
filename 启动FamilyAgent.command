#!/bin/zsh
cd -- "$(dirname -- "$0")"
python3 scripts/start.py
if [ $? -ne 0 ]; then
  echo "启动未完成，请查看上方提示。按回车关闭。"
  read
fi
