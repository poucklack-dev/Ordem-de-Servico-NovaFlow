@echo off
setlocal
set "NODE_DIR=C:\Program Files\nodejs"
if not exist "%NODE_DIR%\node.exe" (
  echo Node.js nao foi encontrado em "%NODE_DIR%".
  echo Instale o Node.js LTS e execute este comando novamente.
  exit /b 1
)
set "PATH=%NODE_DIR%;%PATH%"
call "%NODE_DIR%\npm.cmd" %*
exit /b %ERRORLEVEL%
