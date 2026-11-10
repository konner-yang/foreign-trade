@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYDIR=%~dp0python"
set "PYEXE=%PYDIR%\python.exe"
set "PYCMD="

if exist "%PYEXE%" goto checklocal
goto findsystem

:checklocal
"%PYEXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto damagedlocal
set "PYCMD=%PYEXE%"
goto runmail

:damagedlocal
echo The bundled Python is damaged. It will be downloaded again.
rmdir /s /q "%PYDIR%" >nul 2>nul

:findsystem
call :accept py -3
if not defined PYCMD call :accept python
if not defined PYCMD call :accept python3
if defined PYCMD goto runmail
goto installpython

:installpython
echo.
echo Python was not found. Downloading it into this folder.
echo No administrator permission is required.
echo.
set "PYZIP=%TEMP%\email-python-3.12.10-embed-amd64.zip"
set "PYSHA=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
set "PYURL=https://cdn.npmmirror.com/binaries/python/3.12.10/python-3.12.10-embed-amd64.zip"
set "PYURL2=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
del /f /q "%PYZIP%" >nul 2>nul
call :download "%PYURL%"
if errorlevel 1 call :download "%PYURL2%"
if not exist "%PYZIP%" goto downloadfailed
call :checkhash
if errorlevel 1 goto hashfailed
mkdir "%PYDIR%" >nul 2>nul
tar -xf "%PYZIP%" -C "%PYDIR%"
if errorlevel 1 goto unpackfailed
del /f /q "%PYZIP%" >nul 2>nul
powershell -NoProfile -Command "$p=Join-Path $env:PYDIR 'python312._pth'; $lines=@(Get-Content -LiteralPath $p); if ($lines -notcontains 'import site') { $lines += 'import site' }; Set-Content -LiteralPath $p -Value $lines -Encoding ascii"
"%PYEXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto unpackfailed
set "PYCMD=%PYEXE%"
goto runmail

:download
where curl.exe >nul 2>nul
if not errorlevel 1 (
  curl.exe -L --fail --retry 2 --output "%PYZIP%" "%~1"
  exit /b %ERRORLEVEL%
)
certutil -urlcache -split -f "%~1" "%PYZIP%" >nul
exit /b %ERRORLEVEL%

:checkhash
powershell -NoProfile -Command "$actual=(Get-FileHash -LiteralPath $env:PYZIP -Algorithm SHA256).Hash; if ($actual.ToLower() -ne $env:PYSHA.ToLower()) { exit 1 }"
exit /b %ERRORLEVEL%

:runmail
if not exist "%PYCMD%" goto missingpython
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PYCMD%" "%~dp0start.py"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo The mailbox setup did not finish.
pause
exit /b %RC%

:accept
set "PYCMD="
where %~1 >nul 2>nul
if errorlevel 1 exit /b 1
%* -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "PYPATHFILE=%TEMP%\email-python-path.txt"
del /f /q "%PYPATHFILE%" >nul 2>nul
%* -c "import pathlib,sys; pathlib.Path(r'%PYPATHFILE%').write_text(sys.executable,encoding='ascii')" >nul 2>nul
if not exist "%PYPATHFILE%" exit /b 1
set /p PYCMD=<"%PYPATHFILE%"
del /f /q "%PYPATHFILE%" >nul 2>nul
if not exist "%PYCMD%" (
  set "PYCMD="
  exit /b 1
)
exit /b 0

:missingpython
echo.
echo Python could not be started.
echo Double-click this file again. If it happens twice, contact the administrator.
echo.
pause
exit /b 1

:downloadfailed
echo.
echo Python could not be downloaded.
echo Check the network, then double-click this file again.
echo.
pause
exit /b 1

:hashfailed
del /f /q "%PYZIP%" >nul 2>nul
echo.
echo The Python download was rejected because its checksum did not match.
echo Double-click this file again. If it happens twice, contact the administrator.
echo.
pause
exit /b 1

:unpackfailed
rmdir /s /q "%PYDIR%" >nul 2>nul
echo.
echo Python was downloaded but could not be prepared.
echo Double-click this file again.
echo.
pause
exit /b 1
