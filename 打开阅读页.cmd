@echo off
setlocal
call "%~dp0lit.cmd" reader %*
if errorlevel 1 pause
