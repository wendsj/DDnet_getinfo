

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
* `pyinstaller -F -w -i "res\icon_1.ico" -n DDnetGI "main.py";mv dist\DDnetGI.exe .`
or
* `pyinstaller -F -w -n DDnetGI "main.py";mv dist\DDnetGI.exe .`


3、删除多余部分 **(可选)**

* 删除 `DDnetGI.exe`、`res文件夹` 以外的文件
* **注意**启动会自动生成 `cache文件夹`

