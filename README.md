

## 说明


> 这是一个用deepseek开发的 ==Win端== 简易DDnet实时检测应用
> 可能存在大量bug

## 依赖

1、`PyQt5`
> pip install PyQt5

2、`requests`
> pip install requests

__一并安装__
> pip install PyQt5 requests

__要求__
* `Python ≥ 3.9`
* `Windows系统`


### 打包

1、安装 PyInstaller 编译exe
* `pip install pyinstaller`


2、打包生成exe
* `pyinstaller -F -w -i "res\icon_1.ico" -n DDnetGI "main.py"`
or
`pyinstaller -F -w -n DDnetGI "main.py"`

`生成的exe在dist文件夹`


<br>
<br>
<br>
<br>
<br>
<br>
<p align="right">F26.11.21</p>