#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Download NLTK data needed for sentiment analysis
echo "Downloading language data for sentiment analysis..."
python3 -c "
import nltk
nltk.download('punkt_tab', quiet=True)
nltk.download('averaged_perceptron_tagger', quiet=True)
nltk.download('brown', quiet=True)
nltk.download('conll2000', quiet=True)
nltk.download('movie_reviews', quiet=True)
nltk.download('wordnet', quiet=True)
"

echo ""
echo "============================================"
echo "  TranscriptBot is starting..."
echo "  Open http://localhost:5000 in your browser"
echo "============================================"
echo ""

python3 app.py
