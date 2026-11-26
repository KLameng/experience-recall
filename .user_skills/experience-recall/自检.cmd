@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (py -3 ".user_skills\experience-recall\自检.py" & goto done)
where python >nul 2>nul && (python ".user_skills\experience-recall\自检.py" & goto done)
echo 没找到 Python：请先装 Python 3，或直接用「解压.ps1 / 看这里.txt」里的手动命令。
:done
echo.
pause
