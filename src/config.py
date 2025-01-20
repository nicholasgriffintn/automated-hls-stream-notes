import os
from dotenv import load_dotenv

load_dotenv()

CF_ENDPOINT = "wss://gateway.ai.cloudflare.com/v1"
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_GATEWAY_ID = os.getenv("CF_GATEWAY_ID")
CF_TOKEN = os.getenv("CF_TOKEN")

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
CHUNK_DURATION_SECONDS = 15
CHUNK_SIZE = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_DURATION_SECONDS
OVERLAP_RATIO = 0.25
OVERLAP_SIZE = int(CHUNK_SIZE * OVERLAP_RATIO)

DEFAULT_SUMMARY_INTERVAL_MINUTES = 5
CONTEXT_WINDOW_SIZE = 5
MAX_EMPTY_CHUNKS = 5

DEFAULT_OUTPUT_DIR = "outputs"
SUBDIRS = ['logs', 'transcripts', 'summaries', 'reports']

def validate_config():
    """Validate that all required environment variables are set"""
    if not all([CF_ACCOUNT_ID, CF_GATEWAY_ID, CF_TOKEN]):
        raise ValueError(
            "Missing required environment variables. Please ensure CF_ACCOUNT_ID, "
            "CF_GATEWAY_ID, and CF_TOKEN are set in your .env file."
        ) 