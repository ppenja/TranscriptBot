@echo off
cd /d "%~dp0"

:: Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate
call venv\Scripts\activate.bat

:: Install dependencies
echo Installing dependencies...
pip install -q -r requirements.txt

:: Download NLTK data needed for sentiment analysis
echo Downloading language data for sentiment analysis...
python -c "import nltk; nltk.download('punkt_tab', quiet=True); nltk.download('averaged_perceptron_tagger', quiet=True); nltk.download('brown', quiet=True); nltk.download('conll2000', quiet=True); nltk.download('movie_reviews', quiet=True); nltk.download('wordnet', quiet=True)"

echo.
echo ============================================
echo   TranscriptBot is starting...
echo   Open http://localhost:5000 in your browser
echo ============================================
echo.

python app.py
pause
