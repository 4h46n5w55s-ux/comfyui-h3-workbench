@echo off
rem ComfyUI H3 Video Workbench (skill copy) - start server, open UI
setlocal
set "PY=D:\ComfyUI\python\python.exe"
set "SRV=C:\Users\Administrator\.codex\skills\comfyui-h3-workbench\scripts\h3_workbench_server.py"
if not exist "%PY%" ( echo [ERR] missing %PY% & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:PY -ArgumentList '-X','utf8','-B',$env:SRV -WindowStyle Hidden" >nul 2>&1
ping -n 3 127.0.0.1 >nul
start "" "http://127.0.0.1:8189"
exit /b 0