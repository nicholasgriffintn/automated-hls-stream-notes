import logging
from pathlib import Path
from datetime import datetime
from typing import Dict
import json

def setup_logger(name: str, log_dir: Path) -> logging.Logger:
    """Set up and configure a logger instance"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        file_handler = logging.FileHandler(log_dir / "process.log")
        file_handler.setLevel(logging.ERROR)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    logger.propagate = False
    return logger

def setup_output_directories(base_dir: Path, stream_url: str, subdirs: list) -> tuple[Path, Dict[str, Path]]:
    """Set up output directory structure and return paths"""
    base_dir = Path(base_dir)
    base_dir.mkdir(exist_ok=True)
    
    stream_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_name = Path(stream_url).stem or "stream"
    output_dir = base_dir / f"{stream_name}_{stream_timestamp}"
    output_dir.mkdir(exist_ok=True)
    
    subdir_paths = {
        subdir: output_dir / subdir
        for subdir in subdirs
    }
    
    for subdir in subdir_paths.values():
        subdir.mkdir(exist_ok=True)
    
    return output_dir, subdir_paths

def setup_output_files(output_dir: Path, subdirs: Dict[str, Path]) -> Dict[str, Path]:
    """Set up and return file paths for output files"""
    files = {
        'transcript': subdirs['transcripts'] / "transcript.txt",
        'transcript_json': subdirs['transcripts'] / "transcript.json",
        'interval_summaries': subdirs['summaries'] / "interval_summaries.txt",
        'current_summary': subdirs['summaries'] / "current_summary.txt",
        'final_report': subdirs['reports'] / "final_report.md"
    }
    
    return files

def save_metadata(output_dir: Path, stream_url: str, summary_interval_minutes: int):
    """Save metadata about the stream processing session"""
    metadata = {
        'stream_url': stream_url,
        'start_time': datetime.now().strftime("%Y%m%d_%H%M%S"),
        'summary_interval_minutes': summary_interval_minutes
    }
    
    with open(output_dir / 'metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)

def clean_transcript(text: str) -> str:
    """Clean and normalize transcript text"""
    if not text:
        return ""
    
    text = text.strip()
    
    text = ' '.join(text.split())
    
    return text 