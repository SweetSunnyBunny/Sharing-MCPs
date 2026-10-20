@echo off
echo Starting Discord Scribe...
cd /d "%~dp0"
npx tsx src/index.ts
pause
