@echo off
setlocal
set PYTHONDONTWRITEBYTECODE=1

rem 더블클릭 시 작업 폴더가 배치 파일 위치와 다를 수 있으므로 명시적으로 이동
cd /d "%~dp0" 2>nul
if not exist "app.py" goto :nodir

rem 8501 포트를 잡고 있는 이전 프로세스 정리
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)

where streamlit >nul 2>&1
if errorlevel 1 (
    echo [알림] streamlit 명령을 찾을 수 없어 python -m 으로 실행합니다.
    python -m streamlit run app.py
) else (
    streamlit run app.py
)
echo.
echo [종료] streamlit 이 종료되었습니다. 오류 메시지가 있으면 위를 확인하세요.
echo        패키지 설치: pip install -r requirements.txt
goto :hold

:nodir
echo [오류] app.py 를 찾을 수 없습니다.
echo        현재 폴더: %cd%
echo        run.bat 은 프로젝트 폴더 안에 두고 실행해야 합니다.

:hold
echo.
pause
endlocal
