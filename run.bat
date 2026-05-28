@echo off
set PYTHONDONTWRITEBYTECODE=1
taskkill /f /im python.exe /fi "WINDOWTITLE eq streamlit*" 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a 2>nul
)
streamlit run app.py
