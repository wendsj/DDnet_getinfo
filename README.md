# DDnet_getinfo



## 说明

> 这是一个用deepseek开发的简易DDnet实时检测应用
> 可能存在大量bug，请理性使用

## 依赖

1、`PyQt5`
> pip install PyQt5

2、`requests`
> pip install requests

一并安装
> pip install PyQt5 requests

要求
`Python ≥ 3.9`


### 打包

1、安装 PyInstaller 编译exe
`pip install pyinstaller`


2、打包生成exe
`pyinstaller -F -w -i icon.ico main.py`
or
`pyinstaller -F -w main.py`

